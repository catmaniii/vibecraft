"""bot 自动决策状态 diff watcher。

不 hook Aristaeus / ares 内部决策点(那是 vendor 黑盒),而是在 on_step 周期性
比较 bot 状态,把变化翻译成中文 event 推给手机 UI。

覆盖的"bot 意图"信号:
- 新建造完成(structures 类型计数增加 → "造了 BG / VR / VS / ...")
- 扩张(townhalls 数量增加 → "扩到 N 矿")
- 升级完成(upgrades id set 增加 → "完成闪烁 / 折跃门研究 / ...")
- build_order 完成(False → True,只推一次 → "开局 build 跑完")

显式跳过的:每帧底层 worker 出生/单位出生/资源变化 —— 噪声太大,玩家不需要看。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# 结构 / 升级 id name → 中文短称(SC2 hotkey 缩写;未知的回落用英文 id)
# 神族建筑全用 B/V 系列 hotkey（不要写 PY/NX 等历史误称）
_STRUCT_LABELS = {
    "NEXUS": "BN",
    "PYLON": "BE",
    "ASSIMILATOR": "BA",  # 注：VC 是 sc2 旧梗（VC=Vespene Collector），玩家圈不那么说；hotkey 是 BA
    "GATEWAY": "BG",
    "WARPGATE": "BG(折跃)",
    "FORGE": "BF",
    "CYBERNETICSCORE": "BC",
    "ROBOTICSFACILITY": "VR",
    "ROBOTICSBAY": "VD",
    "STARGATE": "VS",
    "TWILIGHTCOUNCIL": "VT",
    "TEMPLARARCHIVES": "VA",
    "DARKSHRINE": "VB",
    "FLEETBEACON": "VX",
    "PHOTONCANNON": "炮台",
    "SHIELDBATTERY": "电池",
}

_UPGRADE_LABELS = {
    "WARPGATERESEARCH": "折跃门研究",
    "BLINKTECH": "闪烁",
    "CHARGE": "冲锋",
    "PROTOSSGROUNDWEAPONSLEVEL1": "陆军攻 1",
    "PROTOSSGROUNDWEAPONSLEVEL2": "陆军攻 2",
    "PROTOSSGROUNDWEAPONSLEVEL3": "陆军攻 3",
    "PROTOSSGROUNDARMORLEVEL1": "陆军甲 1",
    "PROTOSSGROUNDARMORLEVEL2": "陆军甲 2",
    "PROTOSSGROUNDARMORLEVEL3": "陆军甲 3",
    "PROTOSSSHIELDSLEVEL1": "护盾 1",
    "PROTOSSAIRWEAPONSLEVEL1": "空军攻 1",
    "PROTOSSAIRARMORLEVEL1": "空军甲 1",
    "PSISTORMTECH": "灵能风暴",
    "RESONATINGGLAIVES": "使徒攻速",
    "GRAVITICDRIVE": "棱镜速度",
    "EXTENDEDTHERMALLANCE": "巨像射程",
}


def _label_struct(name: str) -> str:
    return _STRUCT_LABELS.get(name.upper(), name)


def _label_upgrade(name: str) -> str:
    return _UPGRADE_LABELS.get(name.upper(), name)


class DecisionWatcher:
    """状态 diff 跟踪器:在 on_step 周期性调,变化时 emit "bot 决策" event。

    使用:
        watcher = DecisionWatcher(emit_event_fn)
        # on_step 末尾:
        watcher.tick(bot, now)
    """

    # 检查间隔(秒,bot.time 游戏时间)。realtime 模式 22.4 tick/s,5s 约 110 tick
    _INTERVAL_S: float = 1.0

    def __init__(self, emit_event: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit_event
        self._last_check_s: float = 0.0
        self._prev_struct_counts: dict[str, int] = {}
        self._prev_townhall_count: int = 0
        self._prev_upgrade_set: set[str] = set()
        self._build_completed_emitted: bool = False

    def tick(self, bot: Any, now: float) -> None:
        """每帧调,内部按 _INTERVAL_S 节流。"""
        if now - self._last_check_s < self._INTERVAL_S:
            return
        self._last_check_s = now

        # build_order 完成(只推一次)
        runner = getattr(bot, "build_order_runner", None)
        if (
            runner is not None
            and getattr(runner, "build_completed", False)
            and not self._build_completed_emitted
        ):
            self._build_completed_emitted = True
            self._push(now, "bot_action", "开局 build 跑完,转自动运营")

        # 扩张
        th_count = len(getattr(bot, "townhalls", []))
        if th_count > self._prev_townhall_count and self._prev_townhall_count > 0:
            self._push(now, "bot_action", f"扩到 {th_count} 矿")
        self._prev_townhall_count = th_count

        # structures 计数(按 type 名)
        new_struct_counts: dict[str, int] = {}
        for s in getattr(bot, "structures", []):
            tname = str(s.type_id.name)
            new_struct_counts[tname] = new_struct_counts.get(tname, 0) + 1
        # diff:type 计数增加 → 该 type 新造一个
        if self._prev_struct_counts:
            for tname, cnt in new_struct_counts.items():
                prev = self._prev_struct_counts.get(tname, 0)
                if cnt > prev:
                    delta = cnt - prev
                    label = _label_struct(tname)
                    # Nexus / WarpGate 走"扩张"路径,这里跳过避免重复(NEXUS)
                    # WarpGate 是 Gateway 升级,会和 GATEWAY 减计数同时发生 → 都报会噪声大
                    if tname.upper() in ("NEXUS",):
                        continue
                    suffix = f" ×{delta}" if delta > 1 else ""
                    self._push(now, "bot_action", f"造 {label}{suffix}")
        self._prev_struct_counts = new_struct_counts

        # upgrades(state.upgrades 是 set[UpgradeId];str(u) 形如 "UpgradeId.BLINKTECH")
        state = getattr(bot, "state", None)
        cur_upgrades: set[str] = {str(u) for u in state.upgrades} if state is not None else set()
        if self._prev_upgrade_set:
            new_upgrades = cur_upgrades - self._prev_upgrade_set
            for uid in new_upgrades:
                name = uid.split(".")[-1] if "." in uid else uid
                self._push(now, "bot_action", f"完成 {_label_upgrade(name)}")
        self._prev_upgrade_set = cur_upgrades

    def _push(self, now: float, sub_kind: str, text: str) -> None:
        """推 event 帧(§9.4 schema)。"""
        self._emit(
            {
                "type": "event",
                "kind": f"decision.{sub_kind}",
                "ts": round(now, 3),
                "payload": {"text": text},
            }
        )
