import logging
from typing import Optional, List, Dict

from sc2.constants import IS_COLLECTING, ALL_GAS
from sc2.ids.ability_id import AbilityId
from sharpy.managers.core import UnitRoleManager
from sharpy.managers.core.unit_value import buildings_5x5, UnitValue
from sharpy.plans.acts import ActBase
from sc2.ids.buff_id import BuffId
from sc2.units import Units

from sharpy.managers.core.roles import UnitTask
from sc2.unit import Unit, UnitOrder

from sharpy.knowledges import Knowledge
from sharpy.general.zone import Zone

MAX_WORKERS_PER_GAS = 3
ZONE_EVACUATION_POWER_THRESHOLD = -5
BAD_ZONE_POWER_THRESHOLD = -2

# vibecraft: 经济可观测 —— worker 跨基地调度日志走"vibecraft.econtrace"命名空间，
# 保证被 server FileHandler 捕获（vibecraft namespace 已 setLevel(INFO)，root FileHandler DEBUG）。
_vc_transfer_logger = logging.getLogger("vibecraft.econtrace")


class WorkStatus:
    def __init__(self, unit: Unit, available: int, force_exit: bool = False) -> None:
        self.force_exit = force_exit
        self.unit = unit
        self.available = available


class DistributeWorkers(ActBase):
    """Handles idle workers and worker distribution."""

    def __init__(
        self,
        min_gas: Optional[int] = None,
        max_gas: Optional[int] = None,
        aggressive_gas_fill: bool = True,
        evacuate_zones: bool = True,
        leave_builders_alone: bool = True,
    ):
        super().__init__()
        assert min_gas is None or isinstance(min_gas, int)
        assert max_gas is None or isinstance(max_gas, int)

        self.min_gas = min_gas
        self.max_gas = max_gas
        self.aggressive_gas_fill = aggressive_gas_fill
        self.leave_builders_alone = leave_builders_alone
        # evacuate
        self.evacuate_zones = evacuate_zones
        self.active_gas_workers = 0
        self.roles: UnitRoleManager = None
        # self.force_work = False
        # workplace tag to tags of workers there
        self.worker_dict: Dict[int, List[int]] = dict()
        self.work_queue: List[WorkStatus] = []
        self.gas_workers_target = 0
        self.gas_workers_max = 0
        self.only_roles = [UnitTask.Idle, UnitTask.Gathering]

    async def start(self, knowledge: Knowledge):
        await super().start(knowledge)
        self.roles = knowledge.roles

    async def execute(self) -> bool:
        # vibecraft: 2026-07-06 采矿策略 hook —— 每帧根据 knowledge.vibecraft.mining_priority
        # 动态覆写 min_gas/max_gas，再走原 calc_gas_workers_target 逻辑（两字段成对写，防残留）。
        # 首帧缓存构造期原始值，"default" 恢复用（否则会砸掉剧本给的 min_gas=6 等）。
        if not hasattr(self, "_vc_orig_min_gas"):
            self._vc_orig_min_gas = self.min_gas
            self._vc_orig_max_gas = self.max_gas
            self._vc_last_priority: str | None = "<<init>>"  # vibecraft: 优先级变更 trace 用

        _vbc = getattr(getattr(self, "knowledge", None), "vibecraft", None)
        _mining_priority = getattr(_vbc, "mining_priority", None)

        if _mining_priority == "mineral":
            # 优先水晶：水晶先采满，多出的农民才去采气。
            # 总采矿农民用 roles.free_workers.amount（排除 Reserved，与 sharpy 自身 calc 对齐）。
            _total_workers = self.roles.free_workers.amount
            _mineral_ideal = sum(
                int(getattr(th, "ideal_harvesters", 0))
                for th in self.ai.townhalls.ready
            )
            self.max_gas = max(0, _total_workers - _mineral_ideal)
            self.min_gas = None
        elif _mining_priority == "gas":
            # 优先气：气井先采满（每井3农），剩下的才采水晶。
            _gas_count = self.ai.gas_buildings.ready.amount
            self.min_gas = _gas_count * 3
            self.max_gas = None
        else:
            # 默认：恢复构造期缓存的原始值（不写 None，否则砸掉剧本 min_gas=6 等）。
            self.min_gas = self._vc_orig_min_gas
            self.max_gas = self._vc_orig_max_gas

        self.gas_workers_target = self.calc_gas_workers_target()
        # vibecraft: 优先级变更时打一条 INFO trace（非每帧，减少噪音）供 mining_priority_selftest.py 解析。
        if _mining_priority != self._vc_last_priority:  # type: ignore[has-type]
            self._vc_last_priority = _mining_priority
            # 用 vibecraft.* 命名空间确保走 Python 标准 logging（VIBECRAFT_SERVER_LOG_PATH handler 能捕获）。
            logging.getLogger("vibecraft.mining_hook").info(
                "MININGTRACE priority=%s min_gas=%s max_gas=%s gas_wt=%s",
                _mining_priority, self.min_gas, self.max_gas, self.gas_workers_target,
            )
        self.gas_workers_max = len(self.safe_active_gas_buildings) * 3
        self.worker_dict.clear()
        self.calculate_workers()
        self.generate_worker_queue()

        for worker in (
            self.roles.all_from_task(UnitTask.Idle).of_type(UnitValue.worker_types)
            + self.roles.all_from_task(UnitTask.Gathering).idle
        ):  # type: Unit
            # Re-assign idle workers
            if not self.leave_builders_alone or not worker.is_using_ability(UnitValue.build_abilities):
                await self.set_work(worker)

        # Balance workers in bases that have to many
        work_status: Optional[WorkStatus] = None
        for status in self.work_queue:
            if status.available < 0 or status.force_exit:
                work_status = status
                break

        if (
            self.aggressive_gas_fill
            and not work_status
            and self.active_gas_workers < min(self.gas_workers_target, self.gas_workers_max)
        ):
            # Assign work
            for status in self.work_queue:
                if status.unit.type_id in buildings_5x5 and status.unit.assigned_harvesters > 0:
                    work_status = status
                    break

        if (
            not work_status
            and self.gas_workers_target is not None
            and self.active_gas_workers > self.gas_workers_target
        ):
            # We have too many workers in gas
            for status in self.work_queue:
                if status.unit.has_vespene and status.unit.assigned_harvesters > 0:
                    work_status = status
                    work_status.force_exit = True
                    break

        if work_status:
            tags = self.worker_dict.get(work_status.unit.tag, [])
            if tags:
                assign_workers = self.cache.by_tags(tags)
                if assign_workers:
                    assign_worker = assign_workers.furthest_to(work_status.unit)
                    await self.set_work(assign_worker, work_status)

        return True

    @property
    def active_gas_buildings(self) -> Units:
        """All gas buildings that are ready."""
        # todo: filter out gas buildings that do not have a nexus nearby (it has been destroyed)?
        return self.ai.gas_buildings.ready

    @property
    def safe_non_full_gas_buildings(self) -> Units:
        """All gas buildings that are on a safe zone and could use more workers."""
        result = Units([], self.ai)

        for zone in self.zone_manager.our_zones:  # type: Zone
            if zone.is_under_attack:
                continue

            filtered = filter(lambda g: g.surplus_harvesters < 0, zone.gas_buildings)
            result.extend(filtered)

        return result

    @property
    def safe_active_gas_buildings(self) -> Units:
        """All gas buildings that are on a safe zone and could use more workers."""
        result = Units([], self.ai)

        for zone in self.zone_manager.our_zones:  # type: Zone
            if zone.is_under_attack:
                continue

            filtered = filter(lambda g: g.has_vespene, zone.gas_buildings)
            result.extend(filtered)

        return result

    def calc_gas_workers_target(self) -> int:
        """Target count for workers harvesting gas."""
        worker_count = self.roles.free_workers.amount
        max_workers_at_gas = self.active_gas_buildings.amount * MAX_WORKERS_PER_GAS

        estimate = round((worker_count - 8) / 2)
        if self.min_gas is not None:
            estimate = max(estimate, self.min_gas)

        if self.max_gas is not None:
            estimate = min(estimate, self.max_gas)

        return max(0, min(max_workers_at_gas, estimate))

    def add_worker(self, worker: Unit, target: Unit):
        worker_list = self.worker_dict.get(target.tag, [])
        if not worker_list:
            self.worker_dict[target.tag] = worker_list
        worker_list.append(worker.tag)

    def calculate_workers(self):
        if not self.ai.townhalls:
            # can't mine anything
            return

        for worker in self.ai.workers:
            if self.roles.unit_role(worker) not in self.only_roles:
                # Prevent scouts and otherwise reserved units to be part of mining force even if they are mining.
                continue

            # worker.is_gathering
            if worker.orders:
                order: UnitOrder = worker.orders[-1]

                if order.ability.id in IS_COLLECTING and isinstance(order.target, int):
                    obj = self.cache.by_tag(order.target)
                    if obj:
                        # if obj.mineral_contents > 0:
                        if obj.is_mineral_field > 0:
                            townhall = self.ai.townhalls.closest_to(obj)
                            self.add_worker(worker, townhall)
                        elif obj.type_id in ALL_GAS:
                            self.add_worker(worker, obj)

                        if obj.type_id in buildings_5x5:
                            if worker.is_carrying_minerals:
                                self.add_worker(worker, obj)
                            elif worker.is_carrying_vespene:
                                if self.ai.gas_buildings:
                                    gas_building = self.ai.gas_buildings.closest_to(worker)
                                    self.add_worker(worker, gas_building)

                        # self.print(
                        #     f"worker {worker.tag} is {order.ability.id.name} to {order.target} {obj.type_id.name}"
                        # )

    def generate_worker_queue(self):
        self.work_queue.clear()
        self.active_gas_workers = 0

        for building in self.ai.gas_buildings + self.ai.townhalls:
            # vibecraft: 偷矿基地主动 FENCE（双向隔离，2026-06-11 升级）。
            # 旧逻辑只 continue 跳过 stealth Nexus —— 防"路由新农民进来"，但**无法驱逐**已经
            # 漂进来采矿的非 stealth 农民（真机 assigned=5 > 自产=2 持续 1152 帧 DRAIN：主矿
            # 农民倒灌进偷矿基地后卡死在那，没有任何机制把它们赶走）。
            # 升级：stealth 农民是 Reserved（LLM_CONTROLLED）→ calculate_workers 的 only_roles
            # 过滤已把它们排除在 worker_dict 外，故 worker_dict[stealth_nexus] 只含"漂进来的
            # 非 Reserved 主矿农民"。有则发 force_exit（大负 available）让平衡器把它们驱逐回
            # 主矿（复用 enemy-zone 撤离机制 line 232，绝不碰 Reserved stealth 农民）；没有则
            # 跳过（不作为 add 目标，仍防路由）。读取路径：self.knowledge.vibecraft.stealth_townhall_tags。
            _stealth = getattr(getattr(self.knowledge, "vibecraft", None), "stealth_townhall_tags", set())
            if building.tag in _stealth:
                # **tag-aware 驱逐**（2026-06-11 修回归）：只赶"非 stealth 农民"。stealth 自产农民
                # （含刚出生、cache-miss 还没 Reserve 上的——`set_unit_role` 那一帧报 not found）
                # 都在 stealth_worker_tags 里，**绝不驱逐**，否则把自己农民送回主矿（真机 22 次
                # ECONTRACE from_kind=stealth→main、cell 长不起来的根因）。只 role 判（only_roles
                # 过滤 Reserved）不够：un-Reserved 的自产农民会混进 worker_dict → 必须再按 tag 排除。
                _sw = (
                    getattr(getattr(self.knowledge, "vibecraft", None), "stealth_worker_tags", set())
                    or set()
                )
                _here = self.worker_dict.get(building.tag, [])
                _drifters = [t for t in _here if t not in _sw]
                if _drifters:
                    # 改写 worker_dict 为只剩 drifter → execute() 的驱逐**选择**也只会挑 drifter
                    # （否则 furthest_to 可能选中一个 stealth 农民赶走）。
                    self.worker_dict[building.tag] = _drifters
                    self.work_queue.append(WorkStatus(building, -len(_drifters) * 10000, True))
                else:
                    self.worker_dict.pop(building.tag, None)
                continue
            if building.is_ready and building.ideal_harvesters == 0:
                # Ignore empty buildings
                continue

            if not building.is_ready and building.build_progress < 0.9:
                # Ignore buildings that are building and won't finish anytime soon
                continue

            current_workers = len(self.worker_dict.get(building.tag, []))
            zone = self.zone_manager.zone_for_unit(building)
            if zone.is_enemys or zone is None:
                # Exit workers from the zone
                self.work_queue.append(WorkStatus(building, -current_workers * 10000, True))
            elif self.evacuate_zones and zone and zone.needs_evacuation:
                # Exit workers from the zone
                self.work_queue.append(WorkStatus(building, -current_workers * 100, True))
            elif not zone.is_ours:
                # Exit workers from the zone (?), what about long distance mining?
                self.work_queue.append(WorkStatus(building, -current_workers * 10, False))
            elif building.type_id in ALL_GAS:
                # One worker should be inside the gas
                harvesters = min(building.assigned_harvesters, current_workers + 1)
                self.active_gas_workers += harvesters
                if building.is_ready:
                    self.work_queue.append(WorkStatus(building, building.ideal_harvesters - harvesters))
                else:
                    self.work_queue.append(WorkStatus(building, 1 - current_workers))
            else:
                if building.is_ready:
                    self.work_queue.append(WorkStatus(building, building.ideal_harvesters - current_workers))
                else:
                    self.work_queue.append(WorkStatus(building, 8 - current_workers))

        if self.active_gas_workers < self.gas_workers_target:

            def sort_method(tpl: WorkStatus):
                if tpl.unit.type_id in buildings_5x5:
                    return tpl.available
                return tpl.available * 10

        elif self.active_gas_workers > self.gas_workers_target:

            def sort_method(tpl: WorkStatus):
                if tpl.unit.type_id in buildings_5x5:
                    return tpl.available * 10
                return tpl.available

        else:

            def sort_method(tpl: WorkStatus):
                return tpl.available

        self.work_queue.sort(key=sort_method)

        # for queue in self.work_queue:
        #     self.print(f"Queue: {queue.unit.type_id.name} {queue.unit.tag}: {queue.available}")

    async def set_work(self, worker: Unit, last_work_status: Optional[WorkStatus] = None):
        if last_work_status:
            typename = last_work_status.unit.type_id.name
            self.print(
                f"Worker {worker.tag} needs better work! {typename} {last_work_status.unit.tag}: {last_work_status.available}"
            )
        else:
            self.print(f"Worker {worker.tag} needs new work!")
        new_work = self.get_new_work(worker, last_work_status)

        if new_work is None:
            self.print(f"No work to assign worker {worker.tag} to.")
            return True

        if new_work.type_id in buildings_5x5:
            for zone in self.zone_manager.expansion_zones:  # type: Zone
                if zone.center_location.distance_to(new_work.position) < 1:
                    new_work = zone.check_best_mineral_field()
                    break

        if new_work:
            self.print(f"New work found, gathering {new_work.type_id} {new_work.tag}!")
            self.assign_to_work(worker, new_work)

        return True  # Always non-blocking

    def get_new_work(self, worker: Unit, last_work_status: Optional[WorkStatus] = None) -> Optional[Unit]:
        new_work: Optional[WorkStatus] = None

        for status in self.work_queue[::-1]:
            if status == last_work_status:
                continue

            if status.unit.has_vespene:
                if status.available > 0:
                    new_work = status
                    break
            else:
                if status.available > 0:
                    new_work = status
                    break

                if new_work is None:
                    if last_work_status is None or last_work_status.force_exit:
                        new_work = status
                else:
                    if new_work.available == status.available and new_work.unit.distance_to(
                        worker
                    ) > status.unit.distance_to(worker):
                        new_work = status

        if new_work:
            if last_work_status:
                if last_work_status.unit.tag == new_work.unit.tag:
                    # Don't move workers from one job to same job
                    return None

                if new_work.available < 0 and not last_work_status.unit.has_vespene and not last_work_status.force_exit:
                    # Don't move workers from overcrowded mineral mining to another overcrowded mineral mining
                    return None

            new_work.available -= 1
            return new_work.unit
        return None

    def assign_to_work(self, worker: Unit, work: Unit):
        # vibecraft: 经济可观测 —— 农民被调去 work(矿/气) 时，若目标基地 ≠ 来源基地，打一条
        # 结构化日志（ECONTRACE worker_transfer，带 from/to 基地分类 main/natural/expN/stealth
        # + 坐标 + 距离）。现有日志只有偷矿方向的 DRAIN_ALARM + 无标签的 base_saturation 快照，
        # 读不出普通"主矿往自然分矿派农民"。这是该路径的唯一调度 chokepoint（set_work 调用）。
        self._vibecraft_log_transfer(worker, work)
        if worker.has_buff(BuffId.ORACLESTASISTRAPTARGET):
            return  # Worker is in stasis and cannot move

        self.roles.set_task(UnitTask.Gathering, worker)
        townhalls = self.ai.townhalls.ready

        self.roles.set_task(UnitTask.Gathering, worker)

        if worker.is_carrying_resource and townhalls:
            closest = townhalls.closest_to(worker)
            worker(AbilityId.SMART, closest)
            worker.gather(work, queue=True)
        else:
            worker.gather(work)

    # vibecraft: 经济可观测 —— 农民跨基地调度结构化日志（"主矿往分矿派农民"可观测）。
    def _vibecraft_log_transfer(self, worker: Unit, work: Unit) -> None:
        """worker 被调去 work（矿/气）时，若**目标基地 ≠ 来源基地**，打一条 ECONTRACE
        worker_transfer 行（from/to 基地分类 + tag + 坐标 + 距离）。纯诊断，永不抛错。

        来源基地 = 离 worker 当前位置最近的 townhall；目标基地 = 离 work 最近的 townhall。
        同基地内调度（只是换矿点）不打，避免噪音。偷矿驱逐（from_kind=stealth）也会被记上。
        """
        try:
            ths = self.ai.townhalls.ready
            if not ths:
                return
            from_th = ths.closest_to(worker)
            to_th = ths.closest_to(work)
            if from_th.tag == to_th.tag:
                return  # 同一基地内换矿点，不算"派去别的基地"
            _stealth = (
                getattr(getattr(self.knowledge, "vibecraft", None), "stealth_townhall_tags", set())
                or set()
            )
            from_kind = self._vibecraft_base_kind(from_th, ths, _stealth)
            to_kind = self._vibecraft_base_kind(to_th, ths, _stealth)
            dist = from_th.position.distance_to(to_th.position)
            _vc_transfer_logger.info(
                "ECONTRACE worker_transfer tag=%d from_kind=%s from_tag=%d from_pos=(%.1f,%.1f) "
                "to_kind=%s to_tag=%d to_pos=(%.1f,%.1f) dist=%.1f",
                int(worker.tag),
                from_kind,
                int(from_th.tag),
                from_th.position.x,
                from_th.position.y,
                to_kind,
                int(to_th.tag),
                to_th.position.x,
                to_th.position.y,
                dist,
            )
        except Exception:
            pass  # 诊断日志绝不影响游戏

    def _vibecraft_base_kind(self, th: Unit, ready_ths: Units, stealth_tags: set) -> str:
        """把 townhall 分类成 main / natural / expN / stealth。

        - tag 在 stealth_townhall_tags → stealth
        - 否则按到 own_main_zone 的距离排序：最近 = main，次近 = natural，其余 = exp{序号}。
        """
        if int(th.tag) in stealth_tags:
            return "stealth"
        try:
            main_pos = self.zone_manager.own_main_zone.center_location
        except Exception:
            return "base"
        non_stealth = [t for t in ready_ths if int(t.tag) not in stealth_tags]
        ranked = sorted(non_stealth, key=lambda t: t.position.distance_to(main_pos))
        for i, t in enumerate(ranked):
            if t.tag == th.tag:
                if i == 0:
                    return "main"
                if i == 1:
                    return "natural"
                return f"exp{i + 1}"
        return "base"
