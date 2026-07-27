"""Director：串起 IntentParser + DirectiveBoard + Sc2Facade。

每个 tick：
1. board.tick(now) → 收到 BoardEvent[]
2. 对每个 COMMITTED 事件，分派到 facade 调用
3. 把 events log 到 GameSession

玩家话语：
1. parse → IntentParseResult / Ambiguous / Error
2. 成功的话，每个 directive 赋当前 issued_at 后 board.submit
3. 视角控制不走 directive 系统 —— PWA 拖小地图直接经 WS frame view_move 调
   bot.facade.move_camera（见 server/ws.py + bot/auto_combat/protoss/bot.py）
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from vibecraft.bot.event_bus import EventBus
from vibecraft.bot.facade import Sc2Facade, UnitRole
from vibecraft.bot.localization import (
    PRODUCTION_BUILDING_NAMES,
    TECH_BUILDING_NAMES,
    UPGRADE_NAMES,
    VERB_NAMES,
    Localizer,
)
from vibecraft.directives.board import (
    BoardEvent,
    BoardEventKind,
    DirectiveBoard,
)
from vibecraft.directives.models import (
    BuildAtPayload,
    BunkerCargoPayload,
    Directive,
    DropActPayload,
    EngagementConstraintPayload,
    ExpansionOverridePayload,
    GroupAssignPayload,
    GroupClearPayload,
    MovePayload,
    ProductionBlockPayload,
    ProductionOverridePayload,
    RallyPointPayload,
    RepairPayload,
    SalvagePayload,
    ScoutPayload,
    StrategyCancelPayload,
    StrategySetPayload,
    StructureItem,
    StructureMovePayload,
    StructureOverridePayload,
    TacticalObjectivePayload,
    TechOverridePayload,
    UnitClaimPayload,
    UnitReleasePayload,
    ViewFollowPayload,
    WorkerTaskPayload,
)
from vibecraft.directives.types import (
    DirectiveType,
    StageKind,
)
from vibecraft.i18n import t as _i18n_t
from vibecraft.llm.parser import IntentParser
from vibecraft.llm.prompt import ParseContext
from vibecraft.llm.schema import (
    AmbiguousParse,
    ClarificationOption,
    ClarificationRequest,
    IntentParseResult,
    ParseError,
    ParseOutcome,
)
from vibecraft.logging_.session import GameSession
from vibecraft.logging_.types import Event, EventKind, LogStream

if TYPE_CHECKING:
    # sc2 的两个 enum 只在类型注解里用到(运行时按需局部 import,避免顶层依赖 sc2)
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.ids.upgrade_id import UpgradeId

    from vibecraft.bot.task_monitor import TaskMonitor
    from vibecraft.strategy.library import StrategyLibrary

logger = logging.getLogger(__name__)

# Q1 fix (2026-05-29): Gateway/Warpgate 同质化 — 同类型升级后不重复建。
# GATEWAY 升 WARPGATE 后 structures(GATEWAY)=0,若不算等价体会再造一批 BG。
# 同理虫族 HATCHERY→LAIR→HIVE / SPIRE→GREATERSPIRE,人族 CC→OC/PF。
#
# key/value 用字符串名称而非 UnitTypeId 枚举对象,避免测试 fake 导致模块重新加载
# 后枚举实例不同(对象身份不等)的问题。_count_equivalent 用 type_id.name 查表。
_STRUCTURE_EQUIVALENTS_NAMES: dict[str, list[str]] = {
    "GATEWAY": ["GATEWAY", "WARPGATE"],
    "HATCHERY": ["HATCHERY", "LAIR", "HIVE"],
    "LAIR": ["LAIR", "HIVE"],
    "COMMANDCENTER": ["COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS"],
    "SPIRE": ["SPIRE", "GREATERSPIRE"],
}

# WP-A 控制边界可视化：色键 → RGB（后端定义，前端 + debug draw 共用）。
# source=group → f"g{group_id}"；source=command → "cyan"；bot_free 不画框。
_CONTROL_COLORS: dict[str, tuple[int, int, int]] = {
    "cyan": (0, 220, 255),  # 普通指令(command)
    "g1": (255, 230, 0),  # 1 队
    "g2": (255, 140, 0),  # 2 队
    "g3": (255, 0, 200),  # 3 队
    "g4": (150, 90, 255),  # 4 队
    "g5": (0, 255, 120),  # 5 队
}

# 农民类单位排除出 bot_free 统计（避免噪音；被玩家 claim 的农民仍出现在 controlled）。
_NON_ARMY_TYPES: frozenset[str] = frozenset({"PROBE", "DRONE", "SCV"})

# WP-A 游戏内画框配色（debug draw）。指令卡单位=方框,按任务动作(verb)配色;颜色 RGB。
# 与下面 _GROUP_COLORS(圆环/编队色)全部错开。截图微调。
_VERB_COLORS: dict[str, tuple[int, int, int]] = {
    "attack_move": (235, 50, 50),  # 红 attack
    "guard_position": (70, 140, 235),  # 蓝 defend
    "hold_position": (0, 160, 200),  # 蓝青 hold
    "standby": (0, 200, 120),  # 青绿 standby
    "scout": (235, 235, 235),  # 白 scout
    "move_to": (175, 175, 175),  # 灰 move
    "retreat": (255, 130, 0),  # 橙 retreat
    "patrol": (200, 120, 255),  # 薰衣草 patrol
    "focus_fire": (255, 90, 90),  # 浅红 focus
    "harass_workers": (255, 0, 110),  # 玫红 harass
    "group_harass": (255, 80, 0),  # 橙红 group_harass 群骚扰
}
_VERB_DEFAULT_COLOR: tuple[int, int, int] = (210, 210, 210)
# verb → 游戏内英文任务名标签（ASCII，SC2 debug 文字不渲染中文）。
_VERB_LABELS: dict[str, str] = {
    "attack_move": "attack",
    "guard_position": "defend",
    "hold_position": "hold",
    "standby": "standby",
    "scout": "scout",
    "move_to": "move",
    "retreat": "retreat",
    "patrol": "patrol",
    "focus_fire": "focus",
    "harass_workers": "harass",
    "group_harass": "grp_harass",
    "follow": "follow",
    "kite": "kite",
    "lift_target": "lift",
    "cast_ability": "cast",
    "gather": "gather",
    "build": "build",
    "cancel": "cancel",
    # move/scout directive 自身当 verb 时
    "move": "move",
}
# 编队圆环色（按 group_id 1-5），与上面 _VERB_COLORS 全部错开;同步给手机 UI。
_GROUP_COLORS: dict[int, tuple[int, int, int]] = {
    1: (255, 230, 0),  # 黄
    2: (140, 255, 60),  # 亮绿
    3: (255, 60, 150),  # 品红
    4: (160, 90, 255),  # 紫
    5: (60, 180, 255),  # 天蓝
}

# "回家"类 named_spot（己方主基地一侧）。standby 到这些点 = 把部队拉回家/撤退/回防,
# 触发"清掉过期全局 attack 意图"(见 _clear_global_attack_on_pullback)。前压点(瞭望塔/
# 对方分矿等)不在此集 → standby 到那里是前出待命,不清全局攻击。
# few_shot 教 LLM:"撤退/回防/拉回来 = standby 到己方主基地 named_spot:'main'"。
_HOME_NAMED_SPOTS: frozenset[str] = frozenset(
    {"main", "home", "main_base", "base", "natural", "main_ramp"}
)

# 建筑 type_id 名（全大写）→ 可下的 salvage ability 名。
# **2026-06-19 真机验证（get_available_abilities + ActionResult）**：地堡实际可用的 salvage
# ability 是 **SALVAGEEFFECT_SALVAGE**（通用 salvage 效果），不是望文生义的 SALVAGEBUNKER_SALVAGE
# —— 后者真机返回 ActionResult.NotSupported、地堡拆不掉（单测/LLM/设计都误以为是 BUNKER 版，
# 只有真局查 available_abilities 才暴露）。SALVAGEEFFECT_SALVAGE 是通用效果，地堡 + 感应塔都走它。
# 每个 tag 只发一条（多条 queue=False 会互相覆盖致都失效）。
_SALVAGE_ABILITIES: dict[str, list[str]] = {
    "BUNKER": ["SALVAGEEFFECT_SALVAGE"],
    "SENSORTOWER": ["SALVAGEEFFECT_SALVAGE"],
}


@dataclass
class DirectorConfig:
    """运行时配置。"""

    # 2026-05-26 用户:UI 上没暴露 1.5s 反悔按钮 → 反悔窗口去掉,默认立即生效。
    # 保留参数化以便未来恢复公平性延迟(跟真人对手 RTS 反应时间对齐时再调回)。
    commit_delay_s: float = 0.0
    # 玩家文字指令 + LLM 解析结果摘要塞进 LLM 动态 context 的滑动窗口长度。
    # 2026-05-24 用户:扩到整局所有指令(DeepSeek V4 Flash 1M context,占用<2%)。
    # 0 表示不限,直接累积整局。> 0 表示保留最近 N 句。
    recent_command_buffer: int = 0
    snapshot_interval_ticks: int = 45  # ~2s 兜底周期（realtime ~22.4 tick/s × 45 ≈ 2s）
    # 2026-06-03 镜头跟随前瞻：部队移动时镜头落在质心前方多少格（看前方战场）。
    # 太大部队会被推出视野（用户反馈 14 太多）→ 默认 7：部队留下半屏 + 多看前方 ~15 格。
    view_follow_forward_offset: float = 7.0
    # WP-A Task 7: 是否每 tick 往 facade 推受控单位画框清单（debug draw）。
    # False 时推空 list 清屏；build_acceptance 等非 realtime 场景可关以省开销。
    debug_draw_control_boundary: bool = True


@dataclass
class _RecentCommand:
    text: str
    ts: float
    # B 摘要式局内 memory(2026-05-17):parse 完成后回填本条话的 outcome 摘要。
    # 下次 build_parse_context 时一起传给 LLM,让它看到自己上次输出过什么(摘要,
    # 不是完整 JSON;C 完整 multi-turn 才传 JSON)。None = 还没 parse 完(罕见
    # 中途异常) / 历史 buffer 在 parse 前先 push 这条 text。
    outcome_summary: str | None = None
    # #4 用户:历史三层展开 —— 这句话识别出的中文解读 + 它产生的 directive id 列表。
    # 前端展开历史项时:输入文本(text)→ 识别解读(interpretation_zh)→ 这些 directive
    # 对应的卡片 + 当前状态(已完成/等待激活/进行中/已取消/已终止)。
    interpretation_zh: str = ""
    directive_ids: list[str] = field(default_factory=list)
    # 识别失败标记（ParseError）→ 历史条目标红"识别失败"。静态,parse 时定。
    failed: bool = False


@dataclass
class TacticalSquad:
    """B 类 L2 squad 抢占状态（harass / scout / recon）。"""

    directive_id: str
    unit_tags: set[int]
    target: Any  # Point2 or None;recon 聚团门期间临时改 squad center
    move_type: Any  # sharpy MoveType or None
    verb: str
    n_wanted: int
    n_locked: int
    # 2026-05-28 用户 recon 聚团 + 撤退判定
    real_target: Any = None  # recon 聚团门用:真 target 暂存,聚拢后切回
    regroup_started_at: float | None = None  # 聚团 timer 起点(15s 超时 bypass)
    # 2026-05-28 用户 拉式征兵 backfill — 新造好的同类型单位每拍补进 squad,
    # 直到 n_wanted 满足;单位全死 → _release_directive_done(reason='units_lost')
    unit_type: str | None = None  # 征兵类型(Stalker / Zealot / ...) — backfill 用


# A 类 verb（全军 override flag 路径，done_when=None 持续到玩家点 ×）
# 2026-05-28 用户:加 hold(全军坚守 — 聚团到点 + 站住,不回家)
_A_VERBS: frozenset[str] = frozenset({"attack", "defend", "retreat", "vision", "hold"})
# B 类 verb（squad 抢占路径，必带 done_when）；raze/regroup/split/drop MVP 留 on_hold
# recon = 火力侦查 = 中后期成建制小队带战斗力前压试探
_B_VERBS: frozenset[str] = frozenset({"harass", "scout", "recon"})


@dataclass(slots=True)
class Recommendation:
    """bot 推荐玩家可以接的下一阶段剧本(等玩家 confirm,不自动 submit)。

    source 标识推荐来源:
      - default: yaml default_transitions[0]
      - abort:   yaml abort_signals 命中
      - llm:     LLM 兜底(yaml 没匹配上时)
    """

    stage: StageKind
    strategy_id: str
    display_name: str
    reason: str
    source: str  # "default" / "abort" / "llm"


@dataclass(slots=True)
class Tactics:
    """bot 当前内部宏观意图(rule-based 推断,非 sharpy 自带概念)。

    stance 取值:
      attacking / defending / expanding / scouting / harassing / sustaining
    """

    stance: str
    label: str  # 中文 + emoji,直接给 UI 显示
    reason: str  # "优势 Overwhelming,4 BG 折跃完"


# 2026-05-27 Task #341: opening 完成后 N 秒超时 → 自动解锁 cap。
# 默认 120s（~2 分钟），给玩家时间自己切 persistent doctrine。
_SUSTAIN_UNCAP_DELAY_S: float = 120.0
# build-aware sustain 兜底（2026-06-15）：到此 game-time 仍未收到 opening_completed →
# 强制 kick（防 opening_completed 信号不可靠的 build，如 reaper_expand，sustain 永不接管 → 余钱爆）。
# 可经 env 覆盖（精调时并行试不同值）；默认 300s（实测 420 偏晚，开局期已囤）。
_SUSTAIN_FALLBACK_S: float = float(os.environ.get("VIBECRAFT_SUSTAIN_FALLBACK_S", "300"))


class Director:
    """主编排器。"""

    # 镜头跟随焦点重算分频:每 N 次 on_tick 重算一次跟随目标点
    # (2026-06-02 用户:降到 1/8;2026-06-13 用户:再降一半 → 1/16)。
    _VIEW_FOLLOW_REFRESH_DIV: ClassVar[int] = 16
    # 镜头平滑滑动(2026-06-13 用户:切换生硬要平滑):每 tick 朝目标点 lerp 这个比例,
    # ~0.7s 滑到位;距离 < SNAP 格时不再发(防微抖)。
    _VIEW_FOLLOW_GLIDE_ALPHA: ClassVar[float] = 0.3
    _VIEW_FOLLOW_GLIDE_SNAP: ClassVar[float] = 1.5

    # i18n 语言默认值（类级）：__init__ 按 parser.locale 覆盖成实例值。这里给默认是为了
    # 那些用 object.__new__(Director) 绕过 __init__ 的单测 stub 也能读到 _lang（回退 zh）。
    _lang: str = "zh"

    def __init__(
        self,
        facade: Sc2Facade,
        parser: IntentParser,
        session: GameSession,
        board: DirectiveBoard | None = None,
        config: DirectorConfig | None = None,
        library: StrategyLibrary | None = None,
        event_bus: EventBus | None = None,
        bot: Any | None = None,
    ) -> None:
        self.facade = facade
        self.parser = parser
        self.session = session
        # i18n:玩家界面语言（zh/en）= parser.locale（子进程 env VIBECRAFT_LOCALE → IntentParser）。
        # 名称本地化入口 Localizer 与服务端消息 t() 都按这个语言走。
        self._lang = getattr(parser, "locale", "zh") or "zh"
        self._loc = Localizer(self._lang)
        self.config = config or DirectorConfig()
        self.board = board or DirectiveBoard(commit_delay_s=self.config.commit_delay_s)
        self.library = library
        self._recent_commands: list[_RecentCommand] = []
        self._committed_count = 0
        # P5.C: bot backref（sharpy KnowledgeBot 实例；向后兼容：不传则 None）
        self._bot = bot
        # P3.2: task_monitor（需要 event_bus；不传则为 None，所有调用有 None-guard）
        self.task_monitor: TaskMonitor | None
        if event_bus is not None:
            from vibecraft.bot.task_monitor import TaskMonitor

            self.task_monitor = TaskMonitor(board=self.board, event_bus=event_bus)
        else:
            self.task_monitor = None
        # 跟踪 in-flight directive（submit 后 → committed/revoked 前）。
        # Board 的 strategy_set / unit_release 不会进 overlays，需要这层映射才能在
        # COMMITTED 事件里把 directive 取出来 dispatch。
        self._in_flight: dict[str, Directive] = {}
        # 2026-05-25 bug 5:ephemeral directive(MOVE/SCOUT/unit_claim ephemeral/
        # TACTICAL_OBJECTIVE 等)commit 后 pop _in_flight,但 PWA 卡片需要继续
        # 显示直到 task_monitor done(grace 5s)或玩家 ×。这里 store 已 commit
        # 的 ephemeral directive 作为 command_cards source #2(_in_flight 是 #1)。
        # 跟 standing_orders / production_overrides 模式一致(commit 前 append,
        # done/revoke 时 pop)。
        self._committed_directives: dict[str, Directive] = {}
        # P1.2 L3 standing orders（persistent=True 的 unit_claim 不走 _in_flight）
        self.standing_orders: list[Directive] = []
        # P5.E: standing order directive_id → resolved unit tags（sharpy 让位跟踪）
        # 2026-05-24 用户:扩展到所有有 selector 的 directive(MOVE/SCOUT/UNIT_CLAIM
        # ephemeral 等),被指令的单位都 set Reserved 防 sharpy 派别的。
        self._standing_order_tags: dict[str, set[int]] = {}
        # WP-C 撤销恢复栈:directive_id → {tag → 它抢占该 tag 前的归属(prior directive_id 或 None=bot自由)}
        self._displaced: dict[str, dict[int, str | None]] = {}
        # 2026-06-03 用户:单位语意标签注册表 —— tag → {spot(守的地点)/verb(任务)/unit_type}。
        # 指派时记，供"守瞭望塔的追猎去X"这类按语意重选；死亡/释放时剪枝。
        self._unit_semantics: dict[int, dict[str, str]] = {}
        # P2 L4 production overrides（PRODUCTION/TECH/EXPANSION_OVERRIDE 不走 _in_flight）
        self.production_overrides: list[Directive] = []
        # 2026-05-24 ARCHON merge:跟踪正在 morph 的 DT/HT tag(防同帧重复下令)
        self._archon_merging_tags: set[int] = set()
        # 2026-05-24 safe_move:directive_id → (tags, target_dict)。每 tick 用
        # plan_drop_path 顺序 move,到达 target 触发 done。
        # (tags, target_dict, engage):engage=True 沿途 attack-move(2026-06-06)
        self._safe_move_tags: dict[str, tuple[set[int], dict[str, Any], bool]] = {}
        # 2026-05-27 Issue 3:MOVE commit 时 selector 还没 unit(典型"出棱镜然后
        # 飞到 enemy_third" 复合指令)。directive_id → {selector, target_dict, safe,
        # submitted_at}。每 tick re-resolve selector;有 unit 后转 _safe_move_tags
        # (safe=True)或直接派 move(safe=False);90s timeout 放弃 release directive。
        self._pending_move: dict[str, dict[str, Any]] = {}
        # 2026-06-01 Task E:代理建造状态机。directive_id →
        # {probe_tag, point, structure, phase("moving"|"building")}
        self._pending_proxy_build: dict[str, dict[str, Any]] = {}
        # 2026-06-07 玩家折跃"在X刷N兵":directive_id → 该卡登记过的 warp key 集合(每兵种一个)。
        # 仅用于 ×/done 时 facade.cancel_warp 清理;进度/完成由 _exec_production_override 走
        # facade.warp_status 评估(与普通出兵同一套 item 状态机)。
        self._warp_registered: dict[str, set[str]] = {}
        # 2026-06-07 出兵集结点(RALLY_POINT):玩家设的全局集结点 (x,y) + 持有它的 directive id。
        # on_tick 每帧 facade.set_rally_point(覆盖 sharpy 默认 gather_point,一次性 flag 必须每帧)。
        # 单条生效:再设新点覆盖旧的(旧卡标 done);玩家 × → 清,恢复 bot 默认前移。
        self._rally_point: tuple[float, float] | None = None
        self._rally_point_id: str | None = None
        # 2026-06-13 镜头平滑滑动:跟随目标点(army/squad 模式),每 tick lerp 逼近。
        self._view_follow_cam_target: tuple[float, float] | None = None
        # 2026-06-13 持续征兵(auto_enroll / recruit_new):
        # directive_id → {kind: "group"|"claim", group_id|None, unit_type: str, seen: set[int]}
        # 每 tick 对比 facade.resolve_selector 当前全量 tags，把新出现的并入编队或 standing order。
        # 懒清理：directive 不在 _in_flight 且不在 standing_orders 时自动 pop。
        self._recruit_watchers: dict[str, dict[str, Any]] = {}
        # 2026-06-01 Task F:巡逻两点无限往返。directive_id →
        # {tag, points:[(x,y),(x,y)], idx(0|1)}
        self._pending_patrol: dict[str, dict[str, Any]] = {}
        # 2026-05-28 用户:activate_when 激活门。committed 但条件未满足的
        # directive 挂在这里,on_tick 每 tick re-check。
        self._pending_activation: dict[str, Directive] = {}
        # 2026-05-24 用户:完成的 directive 延迟 _DONE_GRACE_S 后才从 list 真删
        # (前 5s 卡片显示"已完成"绿色给玩家看,然后自然消失)
        self._done_at: dict[str, float] = {}
        # #4 用户:历史三层展开 —— directive 终态记录(卡片消失后仍可查)。
        # id → {"status": "completed"|"cancelled"|"terminated", "display": str}。
        # completed=正常完成 / cancelled=玩家手动× / terminated=单位死光(units_lost)。
        self._directive_terminal: dict[str, dict[str, str]] = {}
        # M3 L4 status tracking: directive_id → {"status": "pending"|"active"|"on_hold",
        # "reason": str}。pending = 刚 commit;active = bot.train/research/expand 已生效;
        # on_hold = prereq 缺失或 affordability 不够,等条件满足再 active。
        # snapshot.production_overrides[*].status 透传给 PWA;状态变化时 emit
        # directive.status_changed event。
        self._override_status: dict[str, dict[str, str]] = {}
        # L4 production_override per-item 状态：
        # {directive_id: {unit_type: {"state": "blocked"|"waiting"|"producing"|"done", "reason": str}}}
        # 多兵种合并的 directive 让每条条件单独显示在等什么（缺前置 / 资源不够 / 生产中）。
        self._production_item_status: dict[str, dict[str, dict[str, str]]] = {}
        # 2026-05-23 用户:依赖树自动补齐。用户说"出隐刀",DT 缺 DARKSHRINE → 自动
        # 补建 VC + VD 链。这个 set 记录已 emit auto-prereq directive 的 structure
        # UPPER name,避免每 tick 重复 emit(防 Board 爆炸)。
        self._auto_prereq_emitted: set[str] = set()
        # snapshot / event 推送回调（P0 / P1）
        self._snapshot_callback: Callable[[dict[str, Any]], None] | None = None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        # snapshot 兜底周期计数器
        self._tick_count: int = 0
        # 当前 bot 推荐(snapshot 透传给 PWA,等玩家 confirm 才 submit;不自动转)
        self._pending_recommendation: Recommendation | None = None
        # 玩家已忽略过的推荐(key=(stage,strategy_id)),不再重复推
        self._dismissed_recommendations: set[tuple[StageKind, str]] = set()
        # 当前 bot 内部意图(rule-based,见 _compute_tactics)
        self._tactics: Tactics | None = None
        # 玩家 voice 切剧本但时机已过 → 拦下来等"硬转"确认;(directive, reasons)
        self._pending_force_strategy: tuple[Directive, list[str]] | None = None
        # 2026-05-24:LLM 不确定时给玩家选项 → PWA 弹层等玩家选/取消
        self._pending_clarification: ClarificationRequest | None = None
        # 2026-05-20: opening 完成信号已触发(bot 推),防重复 switch。
        # 一旦 4bg 等开局达到完成条件,触发一次 _apply_auto_persistent_switch
        # ("opening_completed") 后置位,后续不再切。
        self._opening_completed_signaled: bool = False
        # 2026-05-27 Task #341: opening sustain uncap 超时触发。
        # opening_completed_at: notify_opening_completed 触发时的 game_time。
        # sustain_uncap_triggered: latch 防重复(一局只触发一次)。
        self._opening_completed_at: float | None = None
        self._sustain_uncap_triggered: bool = False
        # 2026-06-15 build 效率沙盒：True 时每 tick 强制 defend（bot 只 macro 不主动 moveout），
        # 隔离战斗损耗噪声。由 game_process director_factory 据 GameConfig.sandbox_macro_only 设。
        self._sandbox_macro_only: bool = False
        # 2026-05-23: phase 事件 set。bot 推事件名(如 dt_rush_forward_pylon_ready,
        # dt_rush_dt_killed_worker)进来,_compute_current_phase_id 据此判断
        # Phase.start_at_event 是否触发。事件 set 单调增长,不清空 —— 一旦发生
        # 就永远算 "已 started"(类似 supply/time 阈值过了就不会再倒回)。
        self._phase_events: set[str] = set()
        # P0b Task 12: L2 tactical_objective 状态
        self._tactical_squads: dict[str, TacticalSquad] = {}
        self._tactical_overrides: dict[str, str] = {}
        self._current_l2_global_id: str | None = None
        self._current_l2_global_directive: Directive | None = None
        # Task 7: drop_act — 已实例化的 ActBase dict(by directive_id),防重复注入
        self._active_drop_acts: dict[str, Any] = {}
        # 已 emit auto_drop_act production override 的 unit name set(防重复)
        self._auto_drop_act_emitted: set[str] = set()
        # Task #311 player override e2e: 玩家覆盖时间线(子进程入口从
        # GameConfig.player_actions 写入)。Director.on_tick 到点 _fire_scheduled_action
        # 模拟玩家按 UI 战术按钮。空 list = 生产 / 普通 build_acceptance,什么都不做。
        # 元素 dict 形状: {"at_s": float, "verb": str, "mode": str|None, "target_area": str|None}
        self._scheduled_player_actions: list[dict[str, Any]] = []
        self._fired_player_actions: set[int] = set()  # 防同帧 / 多 tick 重触发
        # Task #350: persistent_doctrine build_acceptance 用。
        # opening_completed + _auto_switch_delay_s 秒后自动 facade.set_build(target)。
        # 由 game_process.director_factory 在 on_start 时写入(参考 _scheduled_player_actions)。
        # 空串 = 不切(生产 / 普通 opening 验收 路径完全不受影响)。
        self._auto_switch_to: str = ""
        self._auto_switch_delay_s: float = 10.0
        self._auto_switch_triggered: bool = False  # latch 防重复
        # 2026-05-30 镜头跟随（view_follow）：当前 active 的跟随指令 id（同时只允许 1 条）。
        # 新 view_follow 到来时旧的自动 superseded（release reason='superseded_by_new_follow'）。
        # 玩家 × → revoke_directive → _apply_view_follow_revoke 清 facade follow。
        self._active_view_follow_id: str | None = None
        # 2026-06-01 锁定跟随 tag：target_kind="unit" 只给 unit_type（无显式 tag）时，
        # 首次 resolve 到的 tag 锁住，后续 tick 一直跟同一单位直到它死（死后重新 resolve）。
        # 不锁会每 tick 取 resolve_selector(unit_type)[0]，集合顺序一变就跳到另一个同型单位
        # （"跟探路农民"在采矿农民间跳的根因之一）。新 view_follow 到来时在 _apply_view_follow 重置。
        self._view_follow_locked_tag: int | None = None
        # 2026-06-02 聚团迟滞：上一次 view_follow army 模式的镜头落点，给迟滞算法用。
        # 新 view_follow 到来时在 _apply_view_follow 重置为 None。
        self._last_view_follow_center: Any | None = None
        # 2026-06-03 镜头前瞻：上一次被跟随单位集的**原始质心**（非偏移后的落点），
        # 给 compute_follow_focus 算移动朝向（质心位移）用。每次更新后写回。
        self._view_follow_prev_centroid: Any | None = None
        # 2026-06-02 用户:镜头跟随刷新降到 1/8 —— 每 _VIEW_FOLLOW_REFRESH_DIV 次
        # on_tick 才真正重发一次镜头(降低跟随抖动/频率)。
        self._view_follow_tick_count: int = 0
        # 2026-05-30 产能封锁（production_block）：{directive_id: unit_type}。
        # 玩家 × → revoke_directive → facade.unblock_production(unit_type)。
        # 每 directive 封锁 1 种兵（MVP；未来可扩 multi-type per directive）。
        self._production_blocks: dict[str, str] = {}  # {directive_id: unit_type}
        # 2026-06-01 语音编队 1-5：group_id(1-5) → 该队的 unit tag 集合。
        # GROUP_ASSIGN 用 SET 语义（替换），GROUP_CLEAR pop + release_unit_role。
        self._voice_groups: dict[int, set[int]] = {}
        # 2026-06-02 连续指令任务链:chain_id → 绑定的 unit tags。第一步解析 selector 后
        # 绑定,后续步骤靠 chain_id 解析回同一单位（同一农民接力走多步）。
        self._task_chains: dict[str, set[int]] = {}
        # 2026-06-06 连续指令"前一步产出的建筑"tag 绑到 chain:农民 build 出建筑的瞬间
        # Director 抓住该建筑 tag 记这里;后续步骤用 activate_when=chain_structure_ready
        # 精确等"那一个"建好(而非全局计数/距离猜)。chain_id → 建好的 structure tags。
        self._chain_structures: dict[str, set[int]] = {}
        # 2026-06-06 代理建造已被某卡"认领"的建筑 tag:同链修两个 VS 时,第2张卡的 settle
        # 检测会撞上第1张卡刚建的那个 VS(同类型、就在旁边)→ 误判"我也建好了"、不再建第2个
        # (真局自验:两个 settle 是同一个 s_tag = 只建了一个)。settle 时把 tag 记这里,
        # 检测时排除已认领的 → 第2张卡必须找一个"没被认领的新建筑",逼它真去建第2个。
        self._proxy_claimed_structs: set[int] = set()
        # 2026-05-30 凤凰骚扰持久指令卡（bot 发起）。PhoenixSquadAct 攒够凤凰 launch
        # 时调 notify_phoenix_harass_started → 这里记 {started_at, deadline} + 渲染卡片。
        # 玩家点× 或 now>=deadline → facade.set_phoenix_harass_active(False) + 清 state
        # → 凤凰归队主力。None = 当前无骚扰卡。卡片 id 固定 "phoenix_harass"。
        self._phoenix_harass: dict[str, float] | None = None
        # WP-E bot 关键动作自评：追踪上次的 bases / 军队人口 + 限频 + 最新一条自评。
        # text: 显示给玩家的一句话; kind: "lost_base"|"lost_army"; ts: game_time。
        self._self_eval_prev_bases: int | None = None
        self._self_eval_prev_army: int | None = None
        self._last_self_eval_t: float = -999.0
        self._bot_self_eval: dict[str, Any] | None = None  # {text, kind, ts}
        # WP-D 实时运营策略层（三维度）
        # 维度1：扩张矿数 override（1-5 / "max" / None=BOT默认）
        # 维度2：农民生产模式（"stop"/"max"/None=默认）
        self._worker_mode: str | None = None
        self._worker_block_dir_id: str | None = None
        # 维度3：采矿策略（"mineral"/"gas"/None=默认）
        self._mining_priority: str | None = None
        # 维度4：攻防升级目标等级（family → int 0-3 / None=auto，只存非 auto 的）
        self._upgrade_targets: dict[str, int | None] = {}
        # WP1 偷矿（2026-06-10）：StealthCellManager 负责 stealth_mine directive 的生命周期。
        # on_tick 每帧驱动所有 cell 状态机（骨架阶段 on_tick 是空壳，WP2-5 填状态机推进）。
        from vibecraft.bot.stealth.manager import StealthCellManager

        self._stealth_manager = StealthCellManager()
        # directive_id ↔ cell_id 双向映射（stealth_mine commit 时写入）
        # 用于 command_cards 里找到对应 cell 的农民数，以及 release 时清 directive 卡
        self._directive_to_cell_id: dict[str, int] = {}
        self._cell_id_to_directive_id: dict[int, str] = {}
        # 2026-06-19 地堡回收预备队：has_cargo 地堡先卸载再拆（SC2 拒绝拆带兵地堡）
        # SALVAGE 分支发 UNLOADALL_BUNKER 后把 tag 加这里；_tick_pending_salvage 等空了再拆。
        self._pending_salvage_tags: set[int] = set()
        # 2026-06-19 通用维修指令：directive_id → Directive（持续型，每 tick 派 SCV 维修）。
        # _tick_repair_orders 每帧检查目标血量，满血/消失后从这里移除并标 done。
        self._repair_orders: dict[str, Directive] = {}
        # 2026-06-29 #580 BC 群骚扰：bc_rush 开局自动提交一条 group_harass claim（只一次；玩家 ❌ 后不再重建）
        self._bc_harass_group_auto_created: bool = False
        # 2026-07-05 harass_workers player claim：撤退中单位 tag 集（回血滞回防抖）。
        # player_should_bail() 按 tag 查/写；claim 取消后多余 tag 自然过期（下次出现时 hp>=recover 会 discard）。
        self._worker_harass_bailing: set[int] = set()
        # 2026-07-08 人族建筑起飞/移动：directive_id → 状态(tag/parent_name/lift_ability/
        # land_ability/land_point)。_tick_structure_move 每 tick(async)推进 LIFT→FLY→LAND。
        self._structure_move_orders: dict[str, dict[str, Any]] = {}
        # 2026-07-08 农民基地调度 transfer_to_base：directive_id → 状态(tags/to_point/
        # expire_at)。_tick_worker_task_transfer 每 tick 重发 gather 令,settle 后释放。
        self._worker_task_transfer_orders: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # snapshot / event 回调注入（P0 / P1）
    # ------------------------------------------------------------------

    def setup_task_monitor(self, event_bus: EventBus) -> None:
        """事后注入 event_bus + 创建 task_monitor（bot on_start 调用，director_factory 不持有 event_bus 时用）。"""
        from vibecraft.bot.task_monitor import TaskMonitor

        self.task_monitor = TaskMonitor(board=self.board, event_bus=event_bus)

    def set_snapshot_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """注入 snapshot 推送回调（game_process 在构造 bot 后调用）。"""
        self._snapshot_callback = cb

    def set_initial_strategy(self, stage: StageKind, strategy_id: str, now: float) -> None:
        """bot 启动时初始化某阶段剧本(反映 ares 选的默认 opening)。

        - bypass board 1.5s commit delay,立即让手机 UI 看到剧本卡片
        - 用 BOT_INTERNAL 来源,玩家 VOICE 指令随时可覆盖
        - 立即 push 一次 snapshot,即使 _snapshot_callback 还没准备好也无害(callback None 时跳过)
        - 幂等:若该 stage slot 已设,不动
        """
        if self.board.set_initial_slot(stage, strategy_id, now):
            self._push_snapshot(now)

    def set_event_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """注入 event 推送回调（P1）。"""
        self._event_callback = cb

    # ------------------------------------------------------------------
    # snapshot 构造（P0-1）
    # ------------------------------------------------------------------

    def _strat_display(self, strat: Any, fallback: str = "") -> str:
        """策略显示名按玩家语言取（#572 批4；en 缺/空 → 回退 zh → 回退 fallback）。

        ⚠️ 仅用于**玩家可见** display；喂 LLM 的 catalog（prompt.py）保持中文，别走这里。
        """
        from vibecraft.strategy.models import localized_name

        return localized_name(strat, self._lang) or fallback

    @staticmethod
    def _compute_current_phase_id(
        phases: list[Any],
        supply_used: int,
        game_time: float,
        events: set[str] | None = None,
    ) -> str | None:
        """根据 supply / time / event 任一触发推断 OpeningBuild 的 current phase id。

        当前 phase = 最后一个"已开始"的 phase。
        phase 已开始 iff (start_at_supply 非 None 且 supply >= 它) 或
                       (start_at_time 非 None 且 time >= 它) 或
                       (start_at_event 非 None 且 event ∈ events)。
        都没 phase 满足时返回第一个 phase id(默认开局)。

        events:bot 触发过的 phase 事件 set(Director._phase_events)。None 等价空集。
        """
        if not phases:
            return None
        ev = events or set()
        current: str = str(phases[0].id)  # 默认第一个
        for p in phases:
            started = False
            if p.start_at_supply is not None and supply_used >= p.start_at_supply:
                started = True
            if p.start_at_time is not None and game_time >= p.start_at_time:
                started = True
            if getattr(p, "start_at_event", None) is not None and p.start_at_event in ev:
                started = True
            if started:
                current = str(p.id)
        return current

    def notify_phase_event(self, event_name: str) -> None:
        """bot 触发一个 phase 事件 —— `_compute_current_phase_id` 据此推 Phase 已开始。

        语义:事件是 latch(单调),一旦发生就永远算 "已 started",不会回退。

        典型场景:
        - "dt_rush_forward_pylon_ready":dt_rush 的 forward PYLON 真建好时
        - "dt_rush_dt_killed_worker":DT 真杀了第一个农民时

        事件名约定加 strategy_id 前缀(如 `dt_rush_xxx`)避免跨 strategy 冲突。
        """
        self._phase_events.add(event_name)

    def build_snapshot(self, now: float) -> dict[str, Any]:
        """组装 snapshot 帧 payload（§1.1 MVP 子集：strategy + recent_commands）。

        library 为 None 时，display 字段 fallback 成 id（向后兼容 + 单测用）。

        M5: MidgameStance / LategameDoctrine 带 attack_window / micro_doctrine 文案，
        让手机 UI 显示"当前剧本的进攻时机"，实现信息透明（bot 行为本身不变）。

        phase stepper(2026-05-16): OpeningBuild slot 带 current_phase_id,
        PWA stepper 据此渲染"已完成 ✓ / 当前 ▶ / 未来 ○"。推断依据
        Phase.start_at_supply / start_at_time 阈值,bot.time / supply_used 从 facade.get_state() 取。
        """
        from vibecraft.strategy.models import (
            LategameDoctrine,
            MidgameStance,
            OpeningBuild,
            PersistentDoctrine,
            localized_name,
            phase_display,
            phase_subtitle,
        )

        # 取一次 bot state 用于 phase tracking(facade 可能 raise,容错)
        try:
            state = self.facade.get_state()
            cur_supply = int(state.supply_used)
            cur_time = float(state.game_time)
        except Exception:
            cur_supply = 0
            cur_time = 0.0

        def _slot_view(stage: StageKind) -> dict[str, Any] | None:
            slot = self.board.slots.get(stage)
            if slot is None:
                return None
            sid = slot.strategy_id
            display = sid  # fallback
            phases: list[dict[str, Any]] | None = None
            attack_window: dict[str, Any] | None = None
            micro_doctrine: list[str] | None = None
            current_phase_id: str | None = None
            if self.library is not None:
                try:
                    strat = self.library.get(sid)
                    display = localized_name(strat, self._lang)
                    if isinstance(strat, OpeningBuild):
                        phases = [
                            {
                                "id": p.id,
                                "display": phase_display(p, self._lang),
                                "subtitle": phase_subtitle(p, self._lang),
                            }
                            for p in strat.phases
                        ]
                        current_phase_id = self._compute_current_phase_id(
                            strat.phases, cur_supply, cur_time, self._phase_events
                        )
                    elif isinstance(strat, MidgameStance):
                        # M5: 透传 attack_window / micro_doctrine，供 PWA 剧本卡片展示
                        if strat.attack_window is not None:
                            attack_window = {
                                "open_at": strat.attack_window.open_at,
                                "close_at": strat.attack_window.close_at,
                            }
                        if strat.micro_doctrine:
                            micro_doctrine = list(strat.micro_doctrine)
                    elif (
                        isinstance(strat, (LategameDoctrine, PersistentDoctrine))
                        and strat.engagement_doctrine
                    ):
                        # M5 + 2026-05-23:lategame/persistent 都用 engagement_doctrine
                        # 作为 micro_doctrine 展示。PersistentDoctrine 是新两层架构 kind,
                        # 之前漏 → PWA 卡片 micro 不显示。
                        # i18n(2026-06-28 #578):en 且有 engagement_doctrine_en 则用英文版,否则回退中文。
                        en_eng = getattr(strat, "engagement_doctrine_en", None)
                        micro_doctrine = (
                            list(en_eng)
                            if (self._lang == "en" and en_eng)
                            else list(strat.engagement_doctrine)
                        )
                except Exception:
                    pass
            entry: dict[str, Any] = {
                "id": sid,
                "display": display,
                # 来源标识:voice(玩家) / auto_transition(剧本完成自动) / bot_internal(开局默认)
                # PWA 据此渲染 badge 区分"玩家安排"vs"bot 自选"
                "set_by": slot.set_by.value,
            }
            if phases is not None:
                entry["phases"] = phases
            if current_phase_id is not None:
                entry["current_phase_id"] = current_phase_id
                # phase 全完成标志:current = 最后一个 phase id
                if phases and current_phase_id == phases[-1]["id"]:
                    entry["all_phases_complete"] = True
            if attack_window is not None:
                entry["attack_window"] = attack_window
            if micro_doctrine is not None:
                entry["micro_doctrine"] = micro_doctrine
            return entry

        snapshot: dict[str, Any] = {
            "type": "snapshot",
            "ts": round(now, 3),
            "strategy": {
                "current_stage": self.board.current_stage.value,
                "opening": _slot_view(StageKind.OPENING),
                "midgame": _slot_view(StageKind.MIDGAME),
                "lategame": _slot_view(StageKind.LATEGAME),
            },
            # #4:富化版在 command_cards 之后用 card index 填(见下方 _build_recent_commands)
            "recent_commands": [],
            # P1.3 L3 standing orders 透传；同时包含 in-flight 的 SCOUT / 非持久 UNIT_CLAIM
            "standing_orders": [
                self._standing_order_view(s)
                for s in (
                    list(self.standing_orders)
                    + [
                        d
                        for d in self._in_flight.values()
                        if d.type in {DirectiveType.SCOUT, DirectiveType.UNIT_CLAIM}
                    ]
                )
            ],
            # P2 L4 production overrides 透传
            "production_overrides": [
                self._production_override_view(s) for s in self.production_overrides
            ],
            # P3.5 active tactical objectives（L2 TACTICAL_OBJECTIVE）。
            # 2026-05-25 bug A 修复:也含 _current_l2_global_directive(commit 后从
            # _in_flight pop 出来但仍在生效),否则玩家 retreat persistent commit 后
            # 卡片消失,既看不到状态也无法 ×。
            "active_tactics": self._build_active_tactics_views(),
        }
        # bot 推荐(玩家未 confirm 前一直 carry,confirm 后清掉)
        if self._pending_recommendation is not None:
            r = self._pending_recommendation
            snapshot["recommendation"] = {
                "stage": r.stage.value,
                "strategy_id": r.strategy_id,
                "display_name": r.display_name,
                "reason": r.reason,
                "source": r.source,
            }
        # bot 内部意图(进攻/守家/开矿/...)
        if self._tactics is not None:
            t = self._tactics
            snapshot["tactics"] = {
                "stance": t.stance,
                "label": t.label,
                "reason": t.reason,
            }
        # 2026-05-28 用户:tactical_debug overlay — 玩家实时看 intent/stance/mode
        # 是否真的写入 vibecraft 命名空间 + PlanZoneAttack.status 真实状态。
        # 跟 telemetry.tactical 同源,但 telemetry 是 2s 一帧落盘 verifier 用,
        # 这里是实时推 PWA。
        if self._bot is not None:
            try:
                from vibecraft.bot.telemetry import extract_tactical_state

                snapshot["tactical_debug"] = extract_tactical_state(self._bot)
            except Exception as exc:
                logger.debug("tactical_debug extract fail: %s", exc)
        # 待玩家确认的"硬转":voice 切剧本但时机已过,被拦下
        if self._pending_force_strategy is not None:
            d, reasons = self._pending_force_strategy
            from vibecraft.directives.models import StrategySetPayload

            payload = d.payload
            if isinstance(payload, StrategySetPayload):
                # 取剧本显示名
                display = payload.strategy_id
                if self.library is not None:
                    try:
                        s = self.library.get(payload.strategy_id)
                        display = self._strat_display(s, payload.strategy_id)
                    except Exception:
                        pass
                snapshot["pending_force_strategy"] = {
                    "stage": payload.stage,
                    "strategy_id": payload.strategy_id,
                    "display_name": display,
                    "source_text": d.source_text or "",
                    "reasons": reasons,
                }
        # 2026-05-24 待玩家选择的 clarification (LLM 给玩家选项时)
        if self._pending_clarification is not None:
            cr = self._pending_clarification
            snapshot["pending_clarification"] = {
                "question": cr.question,
                "source_text": cr.source_text,
                "options": [
                    {
                        "index": i,
                        "label": opt.label,
                        "interpretation_zh": opt.interpretation_zh,
                        "directive_count": len(opt.directives),
                    }
                    for i, opt in enumerate(cr.options)
                ],
            }
        snapshot["voice_groups"] = self._build_voice_groups_view()
        # 编队上限(可配置)透传给 web，让编队条渲染对应数量的槽位。
        # 读运行时全局(由 IntentParser 从 ParserConfig.max_voice_groups 应用)。
        from vibecraft.directives import scope as _scope

        snapshot["max_voice_groups"] = _scope.MAX_VOICE_GROUPS
        # 编队色透传给 web，让手机编队条边框色 = 游戏内圆环色(_GROUP_COLORS,单一真相源)。
        snapshot["group_colors"] = {str(gid): list(rgb) for gid, rgb in _GROUP_COLORS.items()}
        cmd_cards = self._build_command_cards(now)
        snapshot["command_cards"] = cmd_cards
        # WP-A Task 2: 建 label 索引供 _controlled_label_for 复用 command card 中文标签
        self._card_label_index = {c["id"]: c.get("display", "") for c in cmd_cards}
        snapshot["controlled_units"] = self._build_controlled_units_view()
        # #4:历史三层 —— 用 live 卡 index 富化 recent_commands(文本/解读/各 directive 状态)
        snapshot["recent_commands"] = self._build_recent_commands({c["id"]: c for c in cmd_cards})
        # 科技进度 + 产能建筑（仅 bot 在线时才采集；单测 FakeFacade 路径下 _bot=None 跳过）
        if self._bot is not None:
            try:
                snapshot["tech_progress"] = self._build_tech_progress()
            except Exception as exc:
                logger.debug("tech_progress build fail: %s", exc)
            try:
                snapshot["production_buildings"] = self._build_production_buildings()
            except Exception as exc:
                logger.debug("production_buildings build fail: %s", exc)
            try:
                snapshot["army_units"] = self._build_army_units()
            except Exception as exc:
                logger.debug("army_units build fail: %s", exc)
        # WP-E bot 自评：transient，超过 TTL 就发 null（前端旁白自然消失）
        ev = self._bot_self_eval
        snapshot["bot_self_eval"] = (
            ev if (ev is not None and now - ev["ts"] < self._SELF_EVAL_TTL_S) else None
        )
        # WP-D 实时运营策略层（三维度）：透传给 web
        snapshot["worker_mode"] = self._worker_mode
        snapshot["mining_priority"] = self._mining_priority
        # WP6：偷矿 cell 列表（PWA 显示偷矿点）
        # 每个 cell 一项：cell_id / location / worker_count / state / has_gas
        # 无 cell 时 stealth_cells=[]。worker_count 用 len(worker_tags)（含可能死亡的 tag，
        # 与 manager WP4 pruning 节奏一致；对 UI 来说精度够用）。
        snapshot["stealth_cells"] = [
            {
                "cell_id": cell.cell_id,
                "location": [cell.point[0], cell.point[1]],
                "worker_count": len(cell.worker_tags),
                "mineral_workers": max(0, len(cell.worker_tags) - len(cell.gas_worker_tags)),
                "gas_workers": len(cell.gas_worker_tags),
                "state": cell.state.value,
                "has_gas": bool(cell.gas_tags),
            }
            for cell in self._stealth_manager.cells.values()
        ]
        return snapshot

    def _build_voice_groups_view(self) -> list[dict[str, Any]]:
        """snapshot 透传：每个 voice group 的 group_id / units(兵种→数量) / count。

        _bot 为 None 或查不到单位则 units={} 仅透传 count（死单位自然滤掉）。
        """
        out: list[dict[str, Any]] = []
        bot = getattr(self, "_bot", None)
        for gid in sorted(self._voice_groups):
            tags = self._voice_groups[gid]
            comp: dict[str, int] = {}
            if bot is not None:
                for t in tags:
                    try:
                        u = bot.units.by_tag(t)
                    except Exception:
                        u = None
                    if u is not None:
                        name = str(u.type_id.name)
                        comp[name] = comp.get(name, 0) + 1
            out.append({"group_id": gid, "units": comp, "count": len(tags)})
        return out

    # ------------------------------------------------------------------
    # WP-A: 控制边界可视化辅助
    # ------------------------------------------------------------------

    def _group_id_for_directive(self, did: str) -> int | None:
        """返回 directive did 对应的编队 group_id（1-5），找不到返回 None。

        优先读 _group_command_gid（测试/上层可直接注入），
        回退遍历 _in_flight / _committed_directives / standing_orders 找 payload.selector.group_id。
        """
        # 测试可直接注入 _group_command_gid: dict[str, int]
        overrides = getattr(self, "_group_command_gid", None)
        if overrides and did in overrides:
            return overrides[did]

        # 从各 directive 集合里找 payload.selector.group_id
        d = self._find_directive(did)
        if d is None:
            return None
        payload = getattr(d, "payload", None)
        if payload is None:
            return None
        selector = getattr(payload, "selector", None)
        if selector is None:
            return None
        gid = getattr(selector, "group_id", None)
        return int(gid) if gid is not None else None

    def _controlled_label_for(self, did: str) -> str:
        """返回 directive did 的中文显示标签。

        优先读 _card_label_index（build_snapshot 调用后由 command cards 填充），
        回退到 directive 的 payload verb/type 名称，找不到返回空串。
        """
        idx = getattr(self, "_card_label_index", None)
        if idx:
            label = idx.get(did)
            if label:
                return label
        # 回退：从 directive payload 取 verb/type 名
        d = self._find_directive(did)
        if d is None:
            return ""
        payload = getattr(d, "payload", None)
        if payload is None:
            return ""
        # 尝试 task.primary_action.verb → 中文
        task = getattr(payload, "task", None)
        if task is not None:
            action = getattr(task, "primary_action", None)
            if action is not None:
                verb = getattr(action, "verb", None)
                if verb is not None:
                    return (
                        self._loc.verb(str(verb.value))
                        if hasattr(self, "_loc")
                        else str(verb.value)
                    )
        return ""

    def _build_controlled_units_view(self) -> dict[str, Any]:
        """受控单位视图：每条玩家指令/编队一组 + bot_free 桶。WP-A。

        数据契约：
          controlled[i] = {source, directive_id, group_id, label, color, count, composition}
          bot_free = {count, composition}
        source=group(有 group_id) → color=f"g{gid}"；否则 source=command, color="cyan"。
        农民(PROBE/DRONE/SCV)排除出 bot_free 统计。
        """
        bot = getattr(self, "_bot", None)
        # 把己方单位一次性规整成 (tag, type_name) 列表。对 bot.units 不可迭代
        # (测试用 MagicMock)/None 兜底为空，避免裸 for 崩 build_snapshot。
        own_units: list[tuple[int, str]] = []
        raw_units = getattr(bot, "units", None) if bot is not None else None
        try:
            for u in raw_units or []:
                try:
                    own_units.append((int(u.tag), str(u.type_id.name)))
                except Exception:
                    continue
        except TypeError:
            own_units = []  # bot.units 不可迭代
        tag_to_type: dict[int, str] = dict(own_units)

        controlled: list[dict[str, Any]] = []
        claimed_tags: set[int] = set()
        for did, tags in self._standing_order_tags.items():
            comp: dict[str, int] = {}
            for t in tags:
                name = tag_to_type.get(t)
                if name is None:  # 死亡/不存在 → 跳过
                    continue
                comp[name] = comp.get(name, 0) + 1
                claimed_tags.add(t)
            if not comp:
                continue
            gid = self._group_id_for_directive(did)
            source = "group" if gid is not None else "command"
            color = f"g{gid}" if gid is not None else "cyan"
            controlled.append(
                {
                    "source": source,
                    "directive_id": did,
                    "group_id": gid,
                    "label": self._controlled_label_for(did),
                    "color": color,
                    "count": sum(comp.values()),
                    "composition": comp,
                }
            )

        # bot_free = 己方军队单位 − 受控(claimed)，排除农民
        free_comp: dict[str, int] = {}
        for tag, name in own_units:
            if tag in claimed_tags:
                continue
            if name in _NON_ARMY_TYPES:
                continue
            free_comp[name] = free_comp.get(name, 0) + 1

        return {
            "controlled": controlled,
            "bot_free": {"count": sum(free_comp.values()), "composition": free_comp},
        }

    def _directive_verb_and_target(self, did: str) -> tuple[str, tuple[float, float] | None]:
        """查一条受控 directive 的 (任务 verb 字符串, 目标点|None)。

        unit_claim → task.primary_action.verb + target;move/scout → 用 directive
        type 当 verb + payload.target。目标点尽力解析(point/named_spot/camera)，解不出 None。
        """
        d = self._in_flight.get(did) or self._committed_directives.get(did)
        if d is None:
            for so in getattr(self, "standing_orders", []):
                if getattr(so, "id", None) == did:
                    d = so
                    break
        if d is None:
            return ("", None)
        p = getattr(d, "payload", None)
        verb = ""
        tgt_spec = None
        task = getattr(p, "task", None)
        if task is not None:
            action = getattr(task, "primary_action", None)
            if action is not None:
                v = getattr(action, "verb", None)
                verb = str(getattr(v, "value", v) or "")
                tgt_spec = getattr(action, "target", None)
        else:
            tgt_spec = getattr(p, "target", None)
            dt = getattr(d, "type", None)
            verb = str(getattr(dt, "value", dt) or "")
        point: tuple[float, float] | None = None
        if tgt_spec is not None:
            try:
                resolved = self._resolve_target_spec_point(tgt_spec)
                if resolved is not None:
                    point = (float(resolved[0]), float(resolved[1]))
            except Exception:
                point = None
        # 2026-06-06 用户:standby/hold 指令在单位**还在前往途中**时,框标签显示 "move"(去),
        # 到了目标点(进 standby 半径)再显示 "standby"。否则农民刚派出就标 standby 让人困惑。
        if verb in ("standby", "hold_position", "guard_position") and point is not None:
            tags = self._standing_order_tags.get(did, set())
            bot = getattr(self, "_bot", None)
            if tags and bot is not None:
                try:
                    from sc2.position import Point2

                    units = bot.units.tags_in(tags)
                    if units and units.center.distance_to(Point2(point)) > self._STANDBY_RADIUS:
                        verb = "move_to"
                except Exception:
                    pass
        return (verb, point)

    def _push_debug_marks(self) -> None:
        """WP-A: 把受控单位转成"画框清单"推给 facade（每 tick 调用）。

        debug_draw_control_boundary=False 时推空 list（清屏）。
        facade 无 set_debug_marks 方法时静默跳过（hasattr 兜底）。

        **逐单位判定规则**（2026-06-05 用户）：
          - 在某编队里 → shape="ring"（队色 + 队号）。编队优先于指令。
          - 不在任何编队 + 有指令 → shape="box"（verb 配色 + 英文任务名）。
          - 既在编队又有指令 → 用圆环（队号 label），**但指令的目标连线照画**。
        实现：先按编队出环（target=控制本队任一单位且有目标的指令的目标点），
        再对指令出框（只画"不在任何编队"的存活单位）。

        label 文字在质心飘一个（facade 算质心）；shape 每个存活单位都画。
        有目标点的组带 target（质心→target 连线）。**文字只 ASCII**(SC2 debug 不渲染中文)。
        """
        if not hasattr(self.facade, "set_debug_marks"):
            return
        if not self.config.debug_draw_control_boundary:
            self.facade.set_debug_marks([])
            return
        bot = getattr(self, "_bot", None)

        def _alive(raw_tags: Any) -> list[int]:
            out: list[int] = []
            for tag in raw_tags:
                t = int(tag)
                if bot is not None:
                    try:
                        if bot.units.by_tag(t) is None:
                            continue
                    except Exception:
                        continue
                out.append(t)
            return out

        marks: list[dict[str, object]] = []

        # 在任一编队中的 tag（环优先于框，框阶段要排除这些）
        grouped_tags: set[int] = set()
        for gtags in self._voice_groups.values():
            grouped_tags |= {int(t) for t in gtags}

        # 1) 编队 → 圆环（队色 + 队号）。
        #    目标连线：控制本队任一单位且带目标的指令的目标点（"1队去瞭望塔"这类）。
        for gid in sorted(self._voice_groups):
            alive = _alive(self._voice_groups[gid])
            if not alive:
                continue
            alive_set = set(alive)
            target: tuple[float, float] | None = None
            for did, dtags in self._standing_order_tags.items():
                if alive_set & {int(t) for t in dtags}:
                    _, tgt = self._directive_verb_and_target(did)
                    if tgt is not None:
                        target = tgt
                        break
            marks.append(
                {
                    "shape": "ring",
                    "color": _GROUP_COLORS.get(int(gid), _GROUP_COLORS[1]),
                    # 2026-06-06 用户:编队标签显示成 team1/team2(ASCII,SC2 debug 能渲染)
                    "label": f"team{gid}",
                    "tags": alive,
                    "target": list(target) if target is not None else None,
                }
            )

        # 2) 指令 → 方框（verb 配色 + 英文任务名）。只画不在任何编队的存活单位。
        for did, dtags in self._standing_order_tags.items():
            alive = [t for t in _alive(dtags) if t not in grouped_tags]
            if not alive:
                continue
            verb, target = self._directive_verb_and_target(did)
            marks.append(
                {
                    "shape": "box",
                    "color": _VERB_COLORS.get(verb, _VERB_DEFAULT_COLOR),
                    "label": _VERB_LABELS.get(verb, verb or "cmd"),
                    "tags": alive,
                    "target": list(target) if target is not None else None,
                }
            )

        # 3) 出兵集结点(2026-06-08 用户):固定点 → 地面亮绿圆环 + 竖线指天(facade point 锚定)。
        if self._rally_point is not None:
            marks.append(
                {
                    "shape": "ring",
                    "color": (80, 255, 120),  # 亮绿,和编队/指令配色区分
                    "point": list(self._rally_point),
                }
            )

        self.facade.set_debug_marks(marks)

    # ------------------------------------------------------------------
    # WP-E: bot 关键动作自评
    # ------------------------------------------------------------------

    def _maybe_self_eval(self, now: float) -> None:
        """每 tick 检测丢分矿 / 大波损兵，触发一句诚实自评写入 _bot_self_eval。

        - bot 为 None 时直接 return（无 bot = 单测 FakeFacade 路径）。
        - 首次调用只存 prev，不发自评。
        - 限频：两条自评之间至少 _SELF_EVAL_COOLDOWN_S 秒。
        - 丢分矿：townhalls.amount 下降。
        - 大波损兵：supply_army 下降 >= _SELF_EVAL_ARMY_DROP，且当前无玩家全军进攻指令。
        """
        bot = getattr(self, "_bot", None)
        if bot is None:
            return
        try:
            cur_bases = int(bot.townhalls.amount)
        except Exception:
            cur_bases = 0
        try:
            cur_army = int(getattr(bot, "supply_army", 0))
        except Exception:
            cur_army = 0

        # 首次调用：只存 prev，不发自评
        if self._self_eval_prev_bases is None or self._self_eval_prev_army is None:
            self._self_eval_prev_bases = cur_bases
            self._self_eval_prev_army = cur_army
            return

        prev_bases = self._self_eval_prev_bases
        prev_army = self._self_eval_prev_army

        # 限频：更新 prev 但不发新评
        in_cooldown = (now - self._last_self_eval_t) < self._SELF_EVAL_COOLDOWN_S

        if not in_cooldown:
            # 丢分矿检测（优先级高于损兵）
            if cur_bases < prev_bases:
                self._bot_self_eval = {
                    "text": _i18n_t("eval.lostBase", self._lang),
                    "kind": "lost_base",
                    "ts": now,
                }
                self._last_self_eval_t = now
            else:
                # 大波损兵：掉 >= _SELF_EVAL_ARMY_DROP 且当前无玩家全军进攻指令
                army_drop = prev_army - cur_army
                if army_drop >= self._SELF_EVAL_ARMY_DROP and not self._is_player_attacking():
                    self._bot_self_eval = {
                        "text": _i18n_t("eval.lostArmy", self._lang, n=army_drop),
                        "kind": "lost_army",
                        "ts": now,
                    }
                    self._last_self_eval_t = now

        self._self_eval_prev_bases = cur_bases
        self._self_eval_prev_army = cur_army

    def _is_player_attacking(self) -> bool:
        """判断玩家当前是否正在下全军进攻指令。

        保守原则：只有确认当前 L2 global directive 是 attack verb 时才 True，
        其余情况（None / defend / retreat 等）返回 False（损兵评照常发）。
        """
        cur = self._current_l2_global_directive
        if cur is None:
            return False
        try:
            verb = cur.payload.verb  # type: ignore[union-attr]
            return str(verb) == "attack"
        except Exception:
            return False

    def _build_recent_commands(self, card_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """#4 历史三层:每条历史 = 输入文本 + 中文解读 + 它产生的 directive 状态列表。

        directive 状态优先取 live 卡(active/进度/waiting/...),卡片消失后取终态
        (completed/cancelled/terminated),都没有则 ended(已结束已清理)。
        """
        out: list[dict[str, Any]] = []
        for c in self._recent_commands:
            directives = [self._history_directive_view(did, card_by_id) for did in c.directive_ids]
            out.append(
                {
                    "text": c.text,
                    "ts": round(c.ts, 3),
                    # 解析失败/模糊时 interpretation_zh 为空 → 回退 outcome_summary
                    # （失败原因），让历史弹窗里失败指令也有可读解读，不只剩"无可执行指令"。
                    "interpretation_zh": c.interpretation_zh or (c.outcome_summary or ""),
                    "directives": directives,
                    # 整条指令的聚合状态（前端按它给历史条目上色）。
                    "status": self._aggregate_command_status(c.failed, directives),
                }
            )
        return out

    @staticmethod
    def _aggregate_command_status(failed: bool, directives: list[dict[str, Any]]) -> str:
        """整条历史指令的聚合状态（前端上色用）。

        failed（识别失败）最优先；否则按 directive 取最"活跃"的：
        执行中(active) > 等待生效(pending/waiting) > 已终止(terminated) >
        已手动取消(cancelled，全取消才算) > 已完成(completed/ended)。
        无 directive 且非失败 → completed（解析成功但无存活卡，视为已结束）。
        """
        if failed:
            return "failed"
        statuses = {d.get("status", "") for d in directives}
        if not statuses:
            return "completed"
        if "active" in statuses:
            return "active"
        if statuses & {"waiting", "pending"}:
            return "pending"
        if "terminated" in statuses:
            return "terminated"
        if statuses == {"cancelled"}:
            return "cancelled"
        return "completed"

    def _history_directive_view(
        self, did: str, card_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """单条 directive 的历史视图 {id, display, status, progress?}。"""
        card = card_by_id.get(did)
        if card is not None:
            status, progress = self._normalize_history_status(card)
            return {
                "id": did,
                "display": card.get("display", ""),
                "status": status,
                "progress": progress,
            }
        term = self._directive_terminal.get(did)
        if term is not None:
            return {
                "id": did,
                "display": term.get("display", ""),
                "status": term.get("status", "ended"),
                "progress": None,
            }
        return {"id": did, "display": "", "status": "ended", "progress": None}

    @staticmethod
    def _normalize_history_status(
        card: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        """live 卡 status → 历史归一状态 + 进度。

        completed=已完成 / terminated=已终止(单位死光) / waiting=等待激活 /
        pending=等待生效 / active=进行中(带进度)。
        """
        st = card.get("status")
        reason = card.get("status_reason", "")
        if st == "done":
            return ("terminated" if reason in ("units_lost", "superseded") else "completed"), None
        if st == "waiting":
            return "waiting", None
        if st == "pending":
            return "pending", None
        # active:进行中 —— 取第一条带 progress 的 condition 当总进度
        progress: dict[str, Any] | None = None
        for cond in card.get("conditions") or []:
            if cond.get("progress"):
                progress = cond["progress"]
                break
        return "active", progress

    def _build_command_cards(self, now: float) -> list[dict[str, Any]]:
        """统一 command_cards array，透传 4 层 directive 给 PWA（P0f Task 10）。

        每张卡片字段：id / layer / type / display / issued_at / status /
        status_reason / revokable / conditions（L2/L3/L4 有 done_when 时附带）。

        来源：
        - L1  board.slots（strategy slots）
        - L2  _in_flight 中的 TACTICAL_OBJECTIVE（ephemeral）
        - L3  standing_orders（persistent unit_claim）
        - L4  production_overrides（production / tech / expansion / structure）
        """
        cards: list[dict[str, Any]] = []

        # L1 strategy slots
        for stage, slot in self.board.slots.items():
            if slot is None:
                continue
            sid = slot.strategy_id
            display = sid
            if self.library is not None:
                try:
                    strat = self.library.get(sid)
                    display = self._strat_display(strat, sid)
                except Exception:
                    pass
            cards.append(
                {
                    "id": f"l1_{stage.value}",
                    "layer": "L1",
                    "type": "strategy_set",
                    "display": f"{stage.value}: {display}",
                    "issued_at": slot.set_at,
                    "status": "active",
                    "status_reason": "",
                    "revokable": True,
                }
            )

        # L2 active tactics (_in_flight + 2026-05-25 bug 5:已 commit 的
        # ephemeral 也保留卡片直到 done/×)。去重 by id(_in_flight 优先,跟
        # current 状态对齐)。
        l2_sources: list[Directive] = list(self._in_flight.values())
        seen_ids = {d.id for d in l2_sources}
        # _committed_directives:已 commit 的 ephemeral 卡片保留;
        # _pending_activation:等 activate_when 激活的 directive —— 也透传成卡片,
        #   status="waiting"(_dispatch_committed_to_facade 设),前端灰显表示"未激活"
        #   (2026-06-02 用户:有 active_since 条件的卡未满足时应灰色)。
        for d in list(self._committed_directives.values()) + list(
            self._pending_activation.values()
        ):
            if d.id not in seen_ids:
                l2_sources.append(d)
                seen_ids.add(d.id)
        for d in l2_sources:
            if d.type == DirectiveType.TACTICAL_OBJECTIVE:
                from vibecraft.directives.models import TacticalObjectivePayload

                payload = d.payload
                if isinstance(payload, TacticalObjectivePayload):
                    # #5:中文 verb（走 _format_tactical_display → Localizer），不露英文 id
                    display = self._format_tactical_display(payload)
                else:
                    display = "tactical"
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "tactical_objective",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": self._build_conditions(d.id, now),
                        "prerequisites": self._build_prerequisites(d, now),
                    }
                )
            elif d.type in (
                DirectiveType.MOVE,
                DirectiveType.SCOUT,
                DirectiveType.BUILD_AT,
                DirectiveType.UNIT_CLAIM,
                DirectiveType.UNIT_RELEASE,
            ):
                # 2026-05-24 用户:单位命令(棱镜回基地/派农民侦查/守瞭望塔)
                # 之前只进 _in_flight 不显示。加 L2 卡片让玩家能看到 + 撤销。
                display = self._format_unit_directive_display(d.payload)
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": d.type.value,
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": self._build_conditions(d.id, now),
                        "prerequisites": self._build_prerequisites(d, now),
                    }
                )
            elif d.type == DirectiveType.RALLY_POINT:
                # 2026-06-07 出兵集结点卡片:显示集结坐标,玩家可 × 恢复 bot 默认
                pt = self._rally_point if d.id == self._rally_point_id else None
                display = (
                    _i18n_t("rally.display", self._lang, x=f"{pt[0]:.0f}", y=f"{pt[1]:.0f}")
                    if pt is not None
                    else _i18n_t("rally.displayTitle", self._lang)
                )
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "rally_point",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": [],
                    }
                )
            elif d.type == DirectiveType.ENGAGEMENT_CONSTRAINT:
                from vibecraft.directives.models import EngagementConstraintPayload

                payload = d.payload
                stance = payload.stance if isinstance(payload, EngagementConstraintPayload) else ""
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "engagement_constraint",
                        "display": f"stance: {stance}",
                        "issued_at": d.issued_at,
                        "status": "active",
                        "status_reason": "",
                        "revokable": True,
                        "conditions": [],
                    }
                )
            elif d.type == DirectiveType.VIEW_FOLLOW:
                # 2026-05-30 镜头跟随卡片：显示跟随目标，玩家可 × 停止
                payload = d.payload
                if isinstance(payload, ViewFollowPayload):
                    target_kind = getattr(payload, "target_kind", "unit")
                    if target_kind == "army":
                        display = _i18n_t("camera.followArmy", self._lang)
                    elif target_kind == "squad":
                        display = _i18n_t("camera.followSquad", self._lang)
                    else:
                        unit_zh = self._loc.unit(payload.unit_type or "")
                        display = _i18n_t("camera.followUnit", self._lang, unit=unit_zh)
                else:
                    display = _i18n_t("camera.follow", self._lang)
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "view_follow",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": "active",
                        "status_reason": "",
                        "revokable": True,
                        "conditions": [],
                    }
                )
            elif d.type == DirectiveType.PRODUCTION_BLOCK:
                # 2026-05-30 产能封锁卡片：显示封锁的兵种，玩家可 × 解除
                payload = d.payload
                if isinstance(payload, ProductionBlockPayload):
                    unit_zh = self._loc.unit(payload.unit_type)
                    display = _i18n_t("override.stopMaking", self._lang, unit=unit_zh)
                else:
                    display = _i18n_t("override.productionBlock", self._lang)
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "production_block",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": [],
                    }
                )
            elif d.type == DirectiveType.GROUP_ASSIGN:
                # 2026-06-13 持续征兵卡：auto_enroll=True 的 GROUP_ASSIGN 留在 _in_flight，
                # 需要显示一张可撤销卡让玩家 × 停止。（普通 GROUP_ASSIGN 立即 done，不会出现在此）
                from vibecraft.directives.models import GroupAssignPayload as _GAP

                payload = d.payload
                if isinstance(payload, _GAP) and payload.auto_enroll:
                    ut = payload.selector.unit_type
                    unit_zh = self._loc.unit(ut) if ut else _i18n_t("card.unit", self._lang)
                    display = _i18n_t(
                        "card.autoGroup", self._lang, unit=unit_zh, group=payload.group_id
                    )
                    st = self._override_status.get(d.id, {})
                    cards.append(
                        {
                            "id": d.id,
                            "layer": "L3",
                            "type": "group_auto_enroll",
                            "display": display,
                            "issued_at": d.issued_at,
                            "status": st.get("status", "active"),
                            "status_reason": st.get("reason", ""),
                            "revokable": True,
                            "conditions": [],
                        }
                    )
            elif d.type == DirectiveType.STEALTH_MINE:
                # 2026-06-11 偷矿指令卡：显示锚点坐标 + 实时农民数（采矿/采气）
                from vibecraft.directives.models import StealthMinePayload as _SMP

                payload = d.payload
                pt_str = (
                    f"({payload.point[0]:.0f},{payload.point[1]:.0f})"
                    if isinstance(payload, _SMP)
                    else ""
                )
                display = _i18n_t("stealth.mineDisplay", self._lang, site=pt_str)
                st = self._override_status.get(d.id, {})
                # 实时农民数：通过 directive_id → cell_id → StealthCell 查询
                cell_id_for_card = self._directive_to_cell_id.get(d.id)
                cell_for_card = (
                    self._stealth_manager.cells.get(cell_id_for_card)
                    if cell_id_for_card is not None
                    else None
                )
                if cell_for_card is not None:
                    gas_w = len(cell_for_card.gas_worker_tags)
                    mineral_w = len(cell_for_card.worker_tags) - gas_w
                    stealth_workers: dict[str, int] | None = {
                        "mineral": max(0, mineral_w),
                        "gas": gas_w,
                    }
                else:
                    stealth_workers = None
                card: dict[str, Any] = {
                    "id": d.id,
                    "layer": "L2",
                    "type": "stealth_mine",
                    "display": display,
                    "issued_at": d.issued_at,
                    "status": st.get("status", "active"),
                    "status_reason": st.get("reason", ""),
                    "revokable": True,
                    "conditions": [],
                }
                if stealth_workers is not None:
                    card["stealth_workers"] = stealth_workers
                cards.append(card)

        # L3 standing orders (persistent unit_claim)
        # 2026-06-29 #580: group_harass claim 单独出「群卡」（标控制艘数），其余走通用 unit_claim 卡。
        for d in self.standing_orders:
            _so_payload = d.payload
            if (
                isinstance(_so_payload, UnitClaimPayload)
                and _so_payload.task.primary_action.verb.value == "group_harass"
            ):
                from vibecraft.directives.scope import TargetKind as _TK

                _n = len(self._standing_order_tags.get(d.id, set()))
                _act_tgt = _so_payload.task.primary_action.target
                _tgt_str: str | None = None
                if _act_tgt is not None and getattr(_act_tgt, "kind", None) == _TK.NAMED_SPOT:
                    _tgt_str = getattr(_act_tgt, "named_spot", None)
                _SPOT_KEYS: dict[str, str] = {
                    "enemy_main": "harass.tgtMain",
                    "enemy_natural": "harass.tgtNatural",
                    "enemy_third": "harass.tgtThird",
                }
                if _tgt_str is None:
                    _tgt_display = _i18n_t("harass.targetAuto", self._lang)
                else:
                    _tgt_key = _SPOT_KEYS.get(_tgt_str)
                    _tgt_display = _i18n_t(_tgt_key, self._lang) if _tgt_key else _tgt_str
                display = _i18n_t("card.groupHarass", self._lang, n=_n, target=_tgt_display)
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L3",
                        "type": "group_harass",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": self._build_conditions(d.id, now),
                        "prerequisites": self._build_prerequisites(d, now),
                    }
                )
            else:
                display = self._format_standing_order_display(_so_payload)
                # #3 用户:持久指令单位全死 → _release_directive_done(units_lost) 设
                # _override_status[done/units_lost],卡片转暗红"单位全失"再 grace 消失。
                # 不再硬编码 active —— 读 _override_status(缺省 active)。
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L3",
                        "type": "unit_claim",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                        "conditions": self._build_conditions(d.id, now),
                        "prerequisites": self._build_prerequisites(d, now),
                    }
                )

        # L4 production_overrides (production / tech / expansion / structure / drop_act)
        # 2026-05-24 用户:drop_act 是单次战术行为(空投),应归 L2 战术而非 L4 产能。
        for d in self.production_overrides:
            st = self._override_status.get(d.id, {})
            display = self._format_production_override_display(d.payload)
            layer = "L2" if d.type == DirectiveType.DROP_ACT else "L4"
            cards.append(
                {
                    "id": d.id,
                    "layer": layer,
                    "type": d.type.value,
                    "display": display,
                    "issued_at": d.issued_at,
                    "status": st.get("status", "pending"),
                    "status_reason": st.get("reason", ""),
                    "revokable": True,
                    "conditions": self._build_conditions(d.id, now),
                    "prerequisites": self._build_prerequisites(d, now),
                }
            )

        # L2 凤凰骚扰卡（bot 发起的合成卡，不经 _in_flight）。玩家可×让凤凰归队，
        # 倒计时显示距硬性截止还剩多久（到点自动收卡）。
        if self._phoenix_harass is not None:
            started = self._phoenix_harass["started_at"]
            deadline = self._phoenix_harass["deadline"]
            total = max(1, int(deadline - started))
            remaining = max(0, int(deadline - now))
            cards.append(
                {
                    "id": "phoenix_harass",
                    "layer": "L2",
                    "type": "phoenix_harass",
                    "display": _i18n_t("harass.phoenixMinerals", self._lang),
                    "issued_at": started,
                    "status": "active",
                    "status_reason": "",
                    "revokable": True,
                    "conditions": [
                        {
                            "text": _i18n_t("harass.autoReturn", self._lang),
                            "met": False,
                            "progress": {
                                "current": total - remaining,
                                "target": total,
                                "unit": _i18n_t("time.seconds", self._lang),
                            },
                        }
                    ],
                }
            )

        return cards

    def _build_conditions(self, directive_id: str, now: float) -> list[dict[str, Any]]:
        """从 task_monitor 提取该 directive 的 done_when 条件 + 当前进度。

        返回 list of {text, met, progress?}。无 done_when 或 task_monitor 未启返回 []。
        any_of/all_of 展开为多条子条件，单条 done_when 返回 1 条。
        """
        tm = self.task_monitor
        if tm is None:
            return []
        dw = tm._done_when.get(directive_id)
        if not dw:
            return []
        kind = dw.get("kind")
        if kind in ("any_of", "all_of"):
            return [self._describe_condition(sub, directive_id, now) for sub in dw.get("subs", [])]
        return [self._describe_condition(dw, directive_id, now)]

    def _describe_condition(
        self, dw: dict[str, Any], directive_id: str, now: float
    ) -> dict[str, Any]:
        """单条 done_when → {text, met, progress?}。

        - unit_count_built_since: text="造 N 个 X"，progress={current, target}
        - time_elapsed_since: text="N 秒后"，progress={current, target}（秒）
        - 其它 kind: text=kind 描述，无 progress / met（UI 算不出，需 game_state）
        """
        tm = self.task_monitor
        kind = dw.get("kind", "")
        if kind == "unit_count_built_since" and tm is not None:
            target = int(dw.get("value", 0))
            ut = dw.get("unit_type", "")
            ut_zh = self._loc.unit(ut) if ut else ut
            counter_key = ut or "*"
            current = int(tm._unit_built_counts.get(directive_id, {}).get(counter_key, 0))
            cond: dict[str, Any] = {
                "text": _i18n_t("cond.buildN", self._lang, n=target, unit=ut_zh),
                "met": current >= target,
                "progress": {
                    "current": current,
                    "target": target,
                    "unit": _i18n_t("cond.unitCount", self._lang),
                },
            }
            # 多兵种 production_override：附 per-item state（blocked/waiting/producing/done）
            # 让 UI 卡片每条进度行单独显示在等什么（缺前置 / 资源不足 / 生产中）
            item_st = self._production_item_status.get(directive_id, {}).get(ut)
            if item_st:
                cond["state"] = item_st.get("state", "")
                cond["state_reason"] = item_st.get("reason", "")
            return cond
        if kind == "time_elapsed_since" and tm is not None:
            target_s = int(dw.get("seconds", 0))
            ref = dw.get("ref", "directive_issued")
            issued = tm._issued_at.get(directive_id, 0.0)
            elapsed = max(0, int(now - issued)) if ref == "directive_issued" else 0
            return {
                "text": _i18n_t("cond.afterSec", self._lang, n=target_s),
                "met": elapsed >= target_s,
                "progress": {
                    "current": min(elapsed, target_s),
                    "target": target_s,
                    "unit": _i18n_t("cond.unitSec", self._lang),
                },
            }
        if kind == "tech_done":
            up = dw.get("upgrade_id", "")
            return {
                "text": _i18n_t("cond.techDone", self._lang, upg=self._upgrade_zh(up)),
                "met": False,
            }
        if kind == "target_destroyed":
            return {
                "text": _i18n_t("cond.destroyed", self._lang, target=dw.get("target", "")),
                "met": False,
            }
        if kind == "own_army_size_ratio":
            return {
                "text": _i18n_t(
                    "cond.armyRatio", self._lang, op=dw.get("op", ">="), val=dw.get("value", 0)
                ),
                "met": False,
            }
        if kind == "vision_acquired":
            return {
                "text": _i18n_t("cond.visionOf", self._lang, area=dw.get("area", "")),
                "met": False,
            }
        if kind == "enemy_killed_in_area":
            return {
                "text": _i18n_t(
                    "cond.killedIn", self._lang, n=dw.get("value", 0), area=dw.get("area", "")
                ),
                "met": False,
            }
        if kind == "expansion_count":
            return {
                "text": _i18n_t(
                    "cond.expandCount", self._lang, op=dw.get("op", ">="), val=dw.get("value", 0)
                ),
                "met": False,
            }
        if kind == "structure_count":
            st = dw.get("structure_type", "")
            value = int(dw.get("value", 0))
            return {
                "text": _i18n_t(
                    "cond.structCount", self._lang, n=value, struct=self._struct_zh(st)
                ),
                "met": False,
            }
        return {"text": kind or "?", "met": False}

    def _build_prerequisites(self, d: Directive, now: float) -> list[dict[str, Any]]:
        """从 directive.payload.activate_when 提取前置条件 + 当前是否满足。

        返回 list of {text, met}。无 activate_when 返回 []。
        all_of/any_of 递归展开为多条子条件（与 done_when 的展开口径一致）。
        met 复用 _is_activation_satisfied(单条独立 check)。
        """
        aw = getattr(getattr(d, "payload", None), "activate_when", None)
        if aw is None:
            return []
        try:
            return self._describe_activation_tree(aw, d)
        except Exception as exc:  # pragma: no cover - 防御
            logger.debug("_build_prerequisites fail: %s", exc)
            return []

    def _describe_activation_tree(self, aw: Any, directive: Any = None) -> list[dict[str, Any]]:
        """activate_when（可能是 all_of/any_of 容器）→ 扁平的前置条件描述列表。"""
        if hasattr(aw, "model_dump"):
            dw = aw.model_dump(mode="json")
        elif isinstance(aw, dict):
            dw = aw
        else:
            return []
        kind = dw.get("kind", "")
        if kind in ("all_of", "any_of"):
            out: list[dict[str, Any]] = []
            for sub in dw.get("conditions", []):
                out.extend(self._describe_activation_tree(sub, directive))
            return out
        return [self._describe_activation_one(dw, directive)]

    def _describe_activation_one(self, dw: dict[str, Any], directive: Any = None) -> dict[str, Any]:
        """单条 activate_when → {text, met}（中文人话）。met 用独立 check。"""
        kind = dw.get("kind", "")
        op = str(dw.get("op", ">="))
        met = bool(self._is_activation_satisfied(dw, directive))
        if kind == "tech_done":
            up = str(dw.get("upgrade_id", ""))
            return {
                "text": _i18n_t("cond.techDone", self._lang, upg=self._upgrade_zh(up)),
                "met": met,
            }
        if kind == "structure_count":
            st = str(dw.get("structure_type", ""))
            value = int(dw.get("value", 0))
            return {
                "text": _i18n_t(
                    "cond.haveStruct", self._lang, struct=self._struct_zh(st), op=op, val=value
                ),
                "met": met,
            }
        if kind == "expansion_count":
            value = int(dw.get("value", 0))
            return {"text": _i18n_t("cond.expandCount", self._lang, op=op, val=value), "met": met}
        if kind == "unit_arrived":
            area = dw.get("area", "")
            area_str = f"({area[0]}, {area[1]})" if isinstance(area, (list, tuple)) else str(area)
            # 2026-06-06 问题5:群组命令也走这,文字别写死"农民",用通用"单位/队伍到达"
            return {"text": _i18n_t("cond.arrived", self._lang, area=area_str), "met": met}
        return {"text": kind or "?", "met": met}

    def _upgrade_zh(self, name: str) -> str:
        """升级英文名 → 本地化显示名（#5；走 Localizer，i18n 预埋）。"""
        return self._loc.upgrade(name)

    def _struct_zh(self, name: str) -> str:
        """建筑英文名 → 本地化显示名（#5；走 Localizer）。"""
        return self._loc.structure(name) if name else _i18n_t("card.structure", self._lang)

    def _push_snapshot(self, now: float) -> None:
        """推 snapshot（若 callback 已注入）。"""
        if self._snapshot_callback is not None:
            self._snapshot_callback(self.build_snapshot(now))

    def _standing_order_view(self, d: Directive) -> dict[str, Any]:
        """把一条 standing order / scout Directive 转成 snapshot 里的 view dict（P1.3）。

        字段：id / display / issued_at / selector / task_summary。
        """
        from vibecraft.directives.models import ScoutPayload

        payload = d.payload
        display = self._format_standing_order_display(payload)
        view: dict[str, Any] = {
            "id": d.id,
            "display": display,
            "issued_at": d.issued_at,
        }
        if isinstance(payload, UnitClaimPayload):
            view["selector"] = payload.selector.model_dump(mode="json", exclude_none=True)
            view["task_summary"] = payload.task.primary_action.verb.value
        elif isinstance(payload, ScoutPayload):
            view["selector"] = (
                payload.selector.model_dump(mode="json", exclude_none=True)
                if payload.selector
                else {}
            )
            view["task_summary"] = "scout"
        else:
            view["selector"] = {}
            view["task_summary"] = ""
        return view

    def _format_unit_directive_display(self, payload: Any) -> str:
        """L2 单位级 directive 卡片中文 display(2026-05-24 用户)。

        覆盖 MOVE / SCOUT / BUILD_AT / UNIT_CLAIM(ephemeral) / UNIT_RELEASE。
        """
        from vibecraft.directives.models import (
            BuildAtPayload,
            MovePayload,
            ScoutPayload,
            UnitClaimPayload,
            UnitReleasePayload,
        )

        def _unit_zh(t: str | None) -> str:
            if not t:
                return _i18n_t("card.unit", self._lang)
            return self._loc.unit(t)

        def _target_zh(target: Any) -> str:
            ns = getattr(target, "named_spot", None)
            ut = getattr(target, "unit_type", None)
            return ns or ut or "?"

        if isinstance(payload, MovePayload):
            return _i18n_t(
                "card.moveTo",
                self._lang,
                unit=_unit_zh(payload.selector.unit_type),
                target=_target_zh(payload.target),
            )
        if isinstance(payload, ScoutPayload):
            ut = payload.selector.unit_type if payload.selector else None
            return _i18n_t(
                "card.scout", self._lang, unit=_unit_zh(ut), target=_target_zh(payload.target)
            )
        if isinstance(payload, BuildAtPayload):
            st_zh = self._struct_zh(payload.structure_type)
            if payload.point is not None:
                x, y = payload.point
                loc = f"({x:.0f},{y:.0f})"
            else:
                loc = payload.named_spot or "?"
            return _i18n_t("card.buildAt", self._lang, struct=st_zh, loc=loc)
        if isinstance(payload, UnitClaimPayload):
            # ephemeral(persistent=False)走这里(persistent 走 standing_orders)
            verb = self._loc.verb(payload.task.primary_action.verb.value)
            target = payload.task.primary_action.target
            return f"{_unit_zh(payload.selector.unit_type)} {verb} {_target_zh(target)}"
        if isinstance(payload, UnitReleasePayload):
            return _i18n_t("card.release", self._lang, unit=_unit_zh(payload.selector.unit_type))
        return _i18n_t("card.unitCommand", self._lang)

    def _format_standing_order_display(self, payload: Any) -> str:
        """中文人话格式：'{unit_type} {verb} {target_display}'（P1.3）。

        例：'Phoenix patrol natural' / 'Probe 侦查 enemy_main'。
        target_display 优先 named_spot，次 unit_type，fallback '?'。
        """
        from vibecraft.directives.models import ScoutPayload

        if isinstance(payload, ScoutPayload):
            ut = (payload.selector.unit_type if payload.selector else None) or ""
            unit_zh = self._loc.unit(ut) if ut else _i18n_t("card.unit", self._lang)
            target_display = payload.target.named_spot or payload.target.unit_type or "?"
            return _i18n_t("card.scout", self._lang, unit=unit_zh, target=target_display)
        if not isinstance(payload, UnitClaimPayload):
            return _i18n_t("card.unknownStanding", self._lang)
        unit_zh = (
            self._loc.unit(payload.selector.unit_type)
            if payload.selector.unit_type
            else _i18n_t("card.unit", self._lang)
        )
        verb = self._loc.verb(payload.task.primary_action.verb.value)
        target = payload.task.primary_action.target
        target_display = (target.named_spot or target.unit_type if target else None) or "?"
        return f"{unit_zh} {verb} {target_display}"

    def _directive_display_for(self, d: Directive) -> str:
        """#4:任意 directive → 中文 display（历史/终态记录复用各 format 助手）。"""
        from vibecraft.directives.models import (
            EngagementConstraintPayload,
            StrategySetPayload,
            UnitClaimPayload,
        )

        t = d.type
        p = d.payload
        try:
            if t == DirectiveType.TACTICAL_OBJECTIVE:
                return self._format_tactical_display(p)
            if t == DirectiveType.STRATEGY_SET and isinstance(p, StrategySetPayload):
                disp = p.strategy_id
                if self.library is not None:
                    with contextlib.suppress(Exception):
                        disp = self._strat_display(self.library.get(p.strategy_id), p.strategy_id)
                return f"{p.stage}: {disp}"
            if t == DirectiveType.UNIT_CLAIM and isinstance(p, UnitClaimPayload) and p.persistent:
                return self._format_standing_order_display(p)
            if t in (
                DirectiveType.MOVE,
                DirectiveType.SCOUT,
                DirectiveType.BUILD_AT,
                DirectiveType.UNIT_CLAIM,
                DirectiveType.UNIT_RELEASE,
            ):
                return self._format_unit_directive_display(p)
            if t in (
                DirectiveType.PRODUCTION_OVERRIDE,
                DirectiveType.TECH_OVERRIDE,
                DirectiveType.EXPANSION_OVERRIDE,
                DirectiveType.STRUCTURE_OVERRIDE,
                DirectiveType.DROP_ACT,
            ):
                return self._format_production_override_display(p)
            if t == DirectiveType.ENGAGEMENT_CONSTRAINT and isinstance(
                p, EngagementConstraintPayload
            ):
                return _i18n_t("card.engageStance", self._lang, stance=p.stance)
        except Exception as exc:  # pragma: no cover - 防御
            logger.debug("_directive_display_for fail: %s", exc)
        return t.value

    def _find_directive(self, directive_id: str) -> Directive | None:
        """#4:按 id 在各活跃集合里找 directive（终态记录前取 display 用）。"""
        for d in self._in_flight.values():
            if d.id == directive_id:
                return d
        for d in self._committed_directives.values():
            if d.id == directive_id:
                return d
        for d in self._pending_activation.values():
            if d.id == directive_id:
                return d
        for coll in (self.standing_orders, self.production_overrides):
            for d in coll:
                if d.id == directive_id:
                    return d
        return None

    def _record_terminal(self, directive_id: str, status: str, d: Directive | None = None) -> None:
        """#4:记 directive 终态（卡片消失后历史仍可查）。status ∈ completed/cancelled/terminated。"""
        if d is None:
            d = self._find_directive(directive_id)
        display = self._directive_display_for(d) if d is not None else ""
        # 已有终态不覆盖（首个终态为准：手动取消优先于后续 grace 清理）
        if directive_id not in self._directive_terminal:
            self._directive_terminal[directive_id] = {"status": status, "display": display}

    # ------------------------------------------------------------------
    # P2 production_overrides snapshot helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # P3.5 active_tactics snapshot helpers
    # ------------------------------------------------------------------

    def _build_active_tactics_views(self) -> list[dict[str, Any]]:
        """构造 snapshot.active_tactics 列表。

        合并两个来源:
        1. `_in_flight` 里 type==TACTICAL_OBJECTIVE 的 directive
           (submit 后 1.5s commit grace 期 + B 类 squad 类型)
        2. `_current_l2_global_directive` (A 类 attack/defend/retreat 等
           commit 后从 _in_flight pop 出来,但 facade override 仍生效中,
           UI 需要继续显示卡片让玩家可 ×)

        去重:_current 的 id 已在 _in_flight 时不重复加。
        """
        views = [
            self._tactical_view(d)
            for d in self._in_flight.values()
            if d.type == DirectiveType.TACTICAL_OBJECTIVE
        ]
        cur = self._current_l2_global_directive
        if cur is not None and cur.id not in self._in_flight:
            views.append(self._tactical_view(cur))
        return views

    # 战术动词中文表 —— 别名指向 localization.VERB_NAMES["zh"]（i18n 单一数据源）
    _TACTICAL_VERB_ZH: ClassVar[dict[str, str]] = VERB_NAMES["zh"]

    def _format_tactical_display(self, payload: Any) -> str:
        """中文人话格式：'{verb_zh} {target_area}'（P3.5）。

        例：'进攻 enemy_natural' / '探 enemy_main' / '骚扰 (12.5, 34.0)'。
        target_area:named_spot 直显，tuple→坐标，None→'自定'。
        """
        from vibecraft.directives.models import TacticalObjectivePayload

        if not isinstance(payload, TacticalObjectivePayload):
            return _i18n_t("card.unknownTactical", self._lang)
        verb_zh = self._loc.verb(payload.verb)
        if payload.target_area is None:
            target_display = _i18n_t("override.custom", self._lang)
        elif isinstance(payload.target_area, str):
            target_display = payload.target_area
        else:
            # tuple[float, float]
            target_display = f"({payload.target_area[0]}, {payload.target_area[1]})"
        return f"{verb_zh} {target_display}"

    def _tactical_view(self, d: Directive) -> dict[str, Any]:
        """把一条 TACTICAL_OBJECTIVE Directive 转成 snapshot 里的 view dict（P3.5）。

        字段：id / display / verb / target_area / issued_at / attack_mode。
        """
        from vibecraft.directives.models import TacticalObjectivePayload

        payload = d.payload
        display = self._format_tactical_display(payload)
        verb = payload.verb if isinstance(payload, TacticalObjectivePayload) else ""
        attack_mode: str | None = None
        if isinstance(payload, TacticalObjectivePayload):
            if payload.target_area is None:
                target_area: str | None = None
            elif isinstance(payload.target_area, str):
                target_area = payload.target_area
            else:
                target_area = f"({payload.target_area[0]}, {payload.target_area[1]})"
            attack_mode = payload.attack_mode  # "all_in" / "probe" / None
        else:
            target_area = None
        return {
            "id": d.id,
            "display": display,
            "verb": verb,
            "target_area": target_area,
            "issued_at": d.issued_at,
            "attack_mode": attack_mode,
            # 来源标识（voice=玩家 / bot_internal / auto_transition / abort）
            "issued_by": d.issued_by.value,
        }

    # ------------------------------------------------------------------
    # 科技进度 + 产能建筑 snapshot helpers（PWA TechProgressPanel 用）
    # ------------------------------------------------------------------

    # 三族常用升级 UpgradeId 名称列表（大写 .name）
    # 只列玩家关心的核心升级；不列 SC2 内部过渡型 ID。
    _KNOWN_UPGRADE_NAMES: ClassVar[tuple[str, ...]] = (
        # 神族攻防护 3 轴
        "PROTOSSGROUNDWEAPONSLEVEL1",
        "PROTOSSGROUNDWEAPONSLEVEL2",
        "PROTOSSGROUNDWEAPONSLEVEL3",
        "PROTOSSGROUNDARMORSLEVEL1",
        "PROTOSSGROUNDARMORSLEVEL2",
        "PROTOSSGROUNDARMORSLEVEL3",
        "PROTOSSSHIELDSLEVEL1",
        "PROTOSSSHIELDSLEVEL2",
        "PROTOSSSHIELDSLEVEL3",
        # 神族空军攻防
        "PROTOSSAIRWEAPONSLEVEL1",
        "PROTOSSAIRWEAPONSLEVEL2",
        "PROTOSSAIRWEAPONSLEVEL3",
        "PROTOSSAIRARMORSLEVEL1",
        "PROTOSSAIRARMORSLEVEL2",
        "PROTOSSAIRARMORSLEVEL3",
        # 神族兵种
        "WARPGATERESEARCH",
        "CHARGE",  # 叉子冲锋
        "BLINKTECH",  # 追猎闪现
        "ADEPTPIERCINGATTACK",  # 使徒穿刺
        "PSISTORMTECH",  # HT 风暴
        "HALLUCINATION",  # 幻象
        "OBSERVERGRAVITICBOOSTER",  # OB 加速
        "GRAVITICDRIVE",  # 棱镜加速
        "EXTENDEDTHERMALLANCE",  # 巨像射程
        "PHOENIXRANGEUPGRADE",  # 凤凰射程
        "CARRIERLAUNCHSPEEDUPGRADE",  # 航母拦截机发射
        "DARKTEMPLARALASADIR",  # DT 技能
        # 虫族攻防
        "ZERGGROUNDARMORSLEVEL1",
        "ZERGGROUNDARMORSLEVEL2",
        "ZERGGROUNDARMORSLEVEL3",
        "ZERGMELEEWEAPONSLEVEL1",
        "ZERGMELEEWEAPONSLEVEL2",
        "ZERGMELEEWEAPONSLEVEL3",
        "ZERGMISSILEWEAPONSLEVEL1",
        "ZERGMISSILEWEAPONSLEVEL2",
        "ZERGMISSILEWEAPONSLEVEL3",
        "ZERGFLYERWEAPONSLEVEL1",
        "ZERGFLYERWEAPONSLEVEL2",
        "ZERGFLYERWEAPONSLEVEL3",
        "ZERGFLYERARMORSLEVEL1",
        "ZERGFLYERARMORSLEVEL2",
        "ZERGFLYERARMORSLEVEL3",
        # 虫族兵种
        "ZERGLINGATTACKSPEED",  # 小狗攻速
        "ZERGLINGMOVEMENTSPEED",  # 小狗移速
        "BANELINGMOVEMENTSPEED",  # 妖虫滚
        "TUNNELINGCLAWS",  # 蟑螂挖
        "GLIALRECONSTITUTION",  # 蟑螂移速
        "CENTRIFICALHOOKS",  # 妖虫攻速
        "EVOLVEGROOVEDSPINES",  # 刺蛇射程
        "EVOLVEMUSCULARAUGMENTS",  # 刺蛇移速
        "LURKERRANGE",  # 潜伏者射程
        "CHITINOUSPLATING",  # 雷兽甲
        "ANABOLICSYNTHESIS",  # 雷兽速
        "OVERLORDSPEED",  # 老爷机速
        "BURROW",  # 挖洞
        "NEURALPARASITE",  # 感染 HT
        # 人族攻防
        "TERRANINFANTRYWEAPONSLEVEL1",
        "TERRANINFANTRYWEAPONSLEVEL2",
        "TERRANINFANTRYWEAPONSLEVEL3",
        "TERRANINFANTRYARMORSLEVEL1",
        "TERRANINFANTRYARMORSLEVEL2",
        "TERRANINFANTRYARMORSLEVEL3",
        "TERRANVEHICLEANDSHIPARMORSLEVEL1",
        "TERRANVEHICLEANDSHIPARMORSLEVEL2",
        "TERRANVEHICLEANDSHIPARMORSLEVEL3",
        "TERRANVEHICLEWEAPONSLEVEL1",
        "TERRANVEHICLEWEAPONSLEVEL2",
        "TERRANVEHICLEWEAPONSLEVEL3",
        "TERRANSHIPWEAPONSLEVEL1",
        "TERRANSHIPWEAPONSLEVEL2",
        "TERRANSHIPWEAPONSLEVEL3",
        # 人族兵种
        "STIMPACK",  # 兴奋剂
        "COMBATSHIELD",  # 盾
        "PUNISHERGRENADES",  # 手雷
        "SIEGETECH",  # 坦克 siege
        "HIGHCAPACITYBARRELS",  # 掠夺者速
        "DRILLCLAWS",  # 掠夺者地下
        "BATTLECRUISERENABLESPECIALIZATIONS",  # BC 专精
        "MEDIVACINCREASESPEEDBOOST",  # 医疗船加速
        "NEOSTEELFRAME",  # 补给站下蹲
        "HISECAUTOTRACKING",  # 导弹塔追踪
        "BANSHEESPEED",  # 女妖速
        "BANSHEECLOAK",  # 女妖隐身
        "GHOSTCLOAK",  # 幽灵隐身
        "TACNUKESILO",  # 核弹
        "LIBERATORAGRANGEUPGRADE",  # 解放者 AG 射程
    )

    # 升级名 → 中文显示 —— 别名指向 localization.UPGRADE_NAMES["zh"]
    _TECH_ZH_NAMES: ClassVar[dict[str, str]] = UPGRADE_NAMES["zh"]

    # 攻防升级目标等级白名单（family → race）：三族 5 条各 15 条总计。
    # 从 UpgradeId enum 派生（hasattr 校验），确保 family 真实存在。
    # vendor/sharpy tech.py 封顶门 + director dispatch + _build_tech_progress 共用同一组 family。
    _UPGRADE_CAP_FAMILIES: ClassVar[dict[str, str]] = {
        # 神族
        "PROTOSSGROUNDWEAPONS": "Protoss",
        "PROTOSSGROUNDARMORS": "Protoss",
        "PROTOSSSHIELDS": "Protoss",
        "PROTOSSAIRWEAPONS": "Protoss",
        "PROTOSSAIRARMORS": "Protoss",
        # 虫族
        "ZERGMELEEWEAPONS": "Zerg",
        "ZERGMISSILEWEAPONS": "Zerg",
        "ZERGGROUNDARMORS": "Zerg",
        "ZERGFLYERWEAPONS": "Zerg",
        "ZERGFLYERARMORS": "Zerg",
        # 人族
        "TERRANINFANTRYWEAPONS": "Terran",
        "TERRANINFANTRYARMORS": "Terran",
        "TERRANVEHICLEWEAPONS": "Terran",
        "TERRANSHIPWEAPONS": "Terran",
        "TERRANVEHICLEANDSHIPARMORS": "Terran",
    }

    @staticmethod
    def _parse_upgrade(upg_name: str) -> tuple[str, int] | tuple[None, None]:
        """UpgradeId.name → (family, level) 或 (None, None) 非攻防升级。

        例：'PROTOSSGROUNDWEAPONSLEVEL2' → ('PROTOSSGROUNDWEAPONS', 2)
             'BLINKTECH' → (None, None)

        与 _build_tech_progress 用的 _LEVEL_RE 正则同源（保证 family key 一致）。
        """
        import re as _re

        m = _re.match(r"^(.*)LEVEL([123])$", upg_name)
        if not m:
            return None, None
        family = m.group(1)
        level = int(m.group(2))
        # 只拦截 15 族白名单内的攻防升级
        if family not in Director._UPGRADE_CAP_FAMILIES:
            return None, None
        return family, level

    # 三族产能建筑 UnitTypeId 名称（大写 .name）
    _PRODUCTION_BUILDING_NAMES: ClassVar[tuple[str, ...]] = (
        # 神族
        "NEXUS",
        "GATEWAY",
        "WARPGATE",
        "STARGATE",
        "ROBOTICSFACILITY",
        # 虫族
        "HATCHERY",
        "LAIR",
        "HIVE",
        # 人族
        "COMMANDCENTER",
        "ORBITALCOMMAND",
        "PLANETARYFORTRESS",
        "BARRACKS",
        "FACTORY",
        "STARPORT",
    )

    # 产能建筑名 → 中文 —— 别名指向 localization.PRODUCTION_BUILDING_NAMES["zh"]
    _BUILDING_ZH_NAMES: ClassVar[dict[str, str]] = PRODUCTION_BUILDING_NAMES["zh"]

    # 三族兵种 UnitTypeId 名称（大写 .name）；工人排最前，其余按攻击→支援→空军顺序。
    # bot 只有本族单位，其余 units() 为空自动过滤。
    _ARMY_UNIT_NAMES: ClassVar[tuple[str, ...]] = (
        # 神族工人
        "PROBE",
        # 神族兵种
        "ZEALOT",
        "STALKER",
        "ADEPT",
        "SENTRY",
        "HIGHTEMPLAR",
        "DARKTEMPLAR",
        "ARCHON",
        "IMMORTAL",
        "COLOSSUS",
        "DISRUPTOR",
        "OBSERVER",
        "WARPPRISM",
        "PHOENIX",
        "VOIDRAY",
        "ORACLE",
        "CARRIER",
        "TEMPEST",
        "MOTHERSHIP",
        # 虫族工人
        "DRONE",
        # 虫族兵种
        "QUEEN",
        "ZERGLING",
        "BANELING",
        "ROACH",
        "RAVAGER",
        "HYDRALISK",
        "LURKERMP",
        "INFESTOR",
        "SWARMHOSTMP",
        "ULTRALISK",
        "OVERSEER",
        "MUTALISK",
        "CORRUPTOR",
        "BROODLORD",
        "VIPER",
        # 人族工人
        "SCV",
        # 人族兵种
        "MARINE",
        "MARAUDER",
        "REAPER",
        "GHOST",
        "HELLION",
        "WIDOWMINE",
        "SIEGETANK",
        "CYCLONE",
        "THOR",
        "VIKINGFIGHTER",
        "MEDIVAC",
        "LIBERATOR",
        "RAVEN",
        "BANSHEE",
        "BATTLECRUISER",
    )

    # 关键科技建筑（科技行：只体现有/没有 + 建成/建造中，不显示数量）。
    # 解锁科技、通常只留 1 个的建筑；Robo/星门/兵营等有产量的在产能行不重复。
    # 三族全列，bot 只有本族建筑，其余 structures() 为空自动过滤。
    _TECH_BUILDING_NAMES: ClassVar[tuple[str, ...]] = (
        # 神族
        "CYBERNETICSCORE",
        "FORGE",
        "TWILIGHTCOUNCIL",
        "ROBOTICSBAY",
        "FLEETBEACON",
        "TEMPLARARCHIVE",
        "DARKSHRINE",
        # 虫族
        "SPAWNINGPOOL",
        "ROACHWARREN",
        "BANELINGNEST",
        "EVOLUTIONCHAMBER",
        "HYDRALISKDEN",
        "LURKERDENMP",
        "INFESTATIONPIT",
        "SPIRE",
        "GREATERSPIRE",
        "ULTRALISKCAVERN",
        "NYDUSNETWORK",
        # 人族
        "ENGINEERINGBAY",
        "ARMORY",
        "GHOSTACADEMY",
        "FUSIONCORE",
    )

    # 科技建筑名 → 中文 hotkey —— 别名指向 localization.TECH_BUILDING_NAMES["zh"]
    _TECH_BUILDING_ZH_NAMES: ClassVar[dict[str, str]] = TECH_BUILDING_NAMES["zh"]

    def _build_tech_progress(self) -> list[dict[str, Any]]:
        """组装 tech_progress 列表（已完成 + 研究中），供 TechProgressPanel 消费。

        返回格式（新版，按 kind 区分分级/非分级）：
        - leveled: {kind, track_en, name_zh, level, status, progress, researching_level, icon_en, chrono}
        - single:  {kind, upgrade_id, name_en, name_zh, status, progress, icon_en, chrono}

        只含已研究或研究中的升级（未开始不入列表）。
        调用方已有 try/except 保护。
        """
        import re

        bot = self._bot
        state_upgrades: Any
        try:
            # python-sc2 BotAI.state.upgrades 是 frozenset[UpgradeId]
            state_upgrades = bot.state.upgrades  # type: ignore[union-attr]
        except Exception:
            state_upgrades = frozenset()

        # 已完成升级名集合（大写）
        done_names: set[str] = set()
        for upg in state_upgrades:
            name = upg.name if hasattr(upg, "name") else str(upg)
            done_names.add(name)

        # UpgradeId import（研究中检查需要）
        try:
            from sc2.ids.upgrade_id import UpgradeId

            _upgrade_id_available = True
        except ImportError:
            _upgrade_id_available = False

        # ------------------------------------------------------------------
        # chrono boost 检测（只有神族有，失败不影响主流程）
        #
        # 一个升级正被星空加速 ⟺ 它的研究建筑（UPGRADE_RESEARCHED_FROM[upg]）中有结构
        # 带 CHRONOBOOSTENERGYCOST buff 且在研究（有 order）。
        # 不靠 order.ability 名匹配升级枚举名 —— Blizzard 命名不一致会漏检：
        #   ability "FORGERESEARCH_PROTOSSGROUNDARMORLEVEL1"（单数 ARMOR）
        #   vs 枚举  "PROTOSSGROUNDARMORSLEVEL1"（复数 ARMORS）→ 永远匹配不上；
        #   通用 ability "RESEARCH_PROTOSSGROUNDWEAPONS" 不带 LEVEL → 武器也漏。
        # ------------------------------------------------------------------
        chrono_building_types: set[Any] = set()
        try:
            from sc2.ids.buff_id import BuffId

            for s in bot.structures:  # type: ignore[union-attr]
                try:
                    if not getattr(s, "orders", None):
                        continue
                    if not s.has_buff(BuffId.CHRONOBOOSTENERGYCOST):
                        continue
                    chrono_building_types.add(s.type_id)
                except Exception:
                    continue
        except Exception:
            pass

        try:
            from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
        except Exception:
            UPGRADE_RESEARCHED_FROM = {}

        def _upg_chrono(upg_obj: Any) -> bool:
            """upg_obj=UpgradeId 成员；其研究建筑带 chrono buff + 在研究 → True。"""
            try:
                bt = UPGRADE_RESEARCHED_FROM.get(upg_obj)
            except Exception:
                return False
            return bt is not None and bt in chrono_building_types

        # ------------------------------------------------------------------
        # 区分分级 vs 非分级升级名
        # ------------------------------------------------------------------
        _LEVEL_RE = re.compile(r"^(.*)LEVEL([123])$")

        # 收集所有分级 track（track_en → [lv1名, lv2名, lv3名]，按 KNOWN_UPGRADE_NAMES 顺序去重）
        seen_tracks: dict[str, list[str]] = {}  # track_en → [LEVEL1_name, LEVEL2_name, LEVEL3_name]
        non_leveled: list[str] = []  # 非分级升级名（按顺序）

        for upg_name in self._KNOWN_UPGRADE_NAMES:
            m = _LEVEL_RE.match(upg_name)
            if m:
                track_en = m.group(1)
                if track_en not in seen_tracks:
                    seen_tracks[track_en] = [
                        track_en + "LEVEL1",
                        track_en + "LEVEL2",
                        track_en + "LEVEL3",
                    ]
            else:
                if upg_name not in non_leveled:
                    non_leveled.append(upg_name)

        out: list[dict[str, Any]] = []

        # ------------------------------------------------------------------
        # 1. 分级 track 折叠
        # ------------------------------------------------------------------
        for track_en, level_names in seen_tracks.items():
            # 已完成的最高级（0-3）
            level = 0
            for lv_idx, lv_name in enumerate(level_names, start=1):
                if lv_name in done_names:
                    level = lv_idx

            # track 名（按玩家语言）：取 lv1 显示名，去掉级数数字（"+1攻"→"+攻" / "+1 Atk"→"+Atk"）
            lv1_zh = self._loc.upgrade(level_names[0])
            track_zh = re.sub(r"\d+\s*", "", lv1_zh)

            # 检查下一级是否在研究中
            status = "done"
            researching_level: int | None = None
            progress = 100
            icon_en = level_names[level - 1] if level >= 1 else None
            chrono = False

            if level < 3 and _upgrade_id_available:
                next_lv = level + 1
                next_name = level_names[next_lv - 1]  # LEVEL{next_lv} 名
                try:
                    next_upg = UpgradeId[next_name]
                    p = bot.already_pending_upgrade(next_upg)  # type: ignore[union-attr]
                    if p > 0:
                        status = "researching"
                        researching_level = next_lv
                        progress = int(p * 100)
                        icon_en = next_name
                        chrono = _upg_chrono(next_upg)
                except Exception:
                    pass

            # 纳入规则：level>0 或 status==researching
            if level == 0 and status != "researching":
                continue

            # 攻防升级目标等级：从 director 状态读取（None=自动，0-3=手动封顶）
            upgrade_target: int | None = self._upgrade_targets.get(track_en)

            item: dict[str, Any] = {
                "kind": "leveled",
                "track_en": track_en,
                "name_zh": track_zh,
                "level": level,
                "status": status,
                "progress": progress,
                "researching_level": researching_level,
                "icon_en": icon_en,
                "chrono": chrono,
                "target": upgrade_target,  # None=自动，int=手动封顶等级（0-3）
            }
            out.append(item)

        # ------------------------------------------------------------------
        # 2. 非分级单项（来自 _KNOWN_UPGRADE_NAMES 非分级部分）
        # ------------------------------------------------------------------
        # 收集已被分级 track 覆盖的名称（避免重复输出）
        known_leveled_names: set[str] = set()
        for level_names in seen_tracks.values():
            known_leveled_names.update(level_names)

        for upg_name in non_leveled:
            is_done = upg_name in done_names

            if is_done:
                # 从 state_upgrades 取 value
                upg_value = 0
                for upg in state_upgrades:
                    n = upg.name if hasattr(upg, "name") else str(upg)
                    if n == upg_name:
                        upg_value = upg.value if hasattr(upg, "value") else 0
                        break
                out.append(
                    {
                        "kind": "single",
                        "upgrade_id": upg_value,
                        "name_en": upg_name,
                        "name_zh": self._loc.upgrade(upg_name),
                        "status": "done",
                        "progress": 100,
                        "icon_en": upg_name,
                        "chrono": False,
                    }
                )
                continue

            # 未完成 → 看是否在研究中
            if not _upgrade_id_available:
                continue
            try:
                upg_id = UpgradeId[upg_name]
            except KeyError:
                continue
            try:
                p = bot.already_pending_upgrade(upg_id)  # type: ignore[union-attr]
            except Exception:
                continue
            if p > 0:
                chrono_s = _upg_chrono(upg_id)
                out.append(
                    {
                        "kind": "single",
                        "upgrade_id": upg_id.value,
                        "name_en": upg_name,
                        "name_zh": self._loc.upgrade(upg_name),
                        "status": "researching",
                        "progress": int(p * 100),
                        "icon_en": upg_name,
                        "chrono": chrono_s,
                    }
                )

        # ------------------------------------------------------------------
        # 3. done_names 里不在任何已知条目（非 KNOWN 升级）→ 也输出 single done
        # ------------------------------------------------------------------
        known_names: set[str] = known_leveled_names | set(non_leveled)
        for upg in state_upgrades:
            upg_name = upg.name if hasattr(upg, "name") else str(upg)
            if upg_name in known_names:
                continue
            # 不在任何 known 列表 → 作为额外 single done 输出
            out.append(
                {
                    "kind": "single",
                    "upgrade_id": upg.value if hasattr(upg, "value") else 0,
                    "name_en": upg_name,
                    "name_zh": self._loc.upgrade(upg_name),
                    "status": "done",
                    "progress": 100,
                    "icon_en": upg_name,
                    "chrono": False,
                }
            )

        # ------------------------------------------------------------------
        # 4. 关键科技建筑（kind=building）：显示已建成数(count) + 建造中数(pending)
        #    2026-06-08 用户:科技建筑也要像产能建筑一样显示"有几个、几个在建造中"
        #    （原来只显示存在/不存在 + 单个进度%，多个同类科技建筑数不出来）。
        #    count=ready 数(蓝色右上角标)，pending=not_ready 数(黄色右下角标)，
        #    progress 仍是最接近完工那个的进度(底部黄条);status=done(有 ready)/building。
        # ------------------------------------------------------------------
        try:
            from sc2.ids.unit_typeid import UnitTypeId as _BUTI
        except ImportError:
            _BUTI = None
        if _BUTI is not None:
            for b_name in self._TECH_BUILDING_NAMES:
                try:
                    tid = _BUTI[b_name]
                except KeyError:
                    continue
                try:
                    bs = bot.structures(tid)  # type: ignore[union-attr]
                    ready_n = bs.ready.amount
                    not_ready = bs.not_ready
                    not_ready_n = not_ready.amount
                except Exception:
                    continue
                if ready_n > 0:
                    b_status, b_progress = "done", 100
                elif not_ready:
                    # 最接近完工的那个的进度（底部黄条）
                    try:
                        b_progress = int(max(float(u.build_progress) for u in not_ready) * 100)
                    except Exception:
                        b_progress = 0
                    b_status = "building"
                else:
                    continue  # 没有 → 不显示（"有/没有"靠存在与否体现）
                out.append(
                    {
                        "kind": "building",
                        "name_en": b_name,
                        "name_zh": self._loc.structure(b_name),
                        "status": b_status,
                        "progress": b_progress,
                        "icon_en": b_name,
                        "count": int(ready_n),  # 已建成数（蓝色右上角标）
                        "pending": int(not_ready_n),  # 建造中数（黄色右下角标）
                    }
                )

        return out

    def _build_production_buildings(self) -> list[dict[str, Any]]:
        """组装 production_buildings 列表（已造好 + 建造中）。

        返回格式：
        [{building_id, name_en, name_zh, count, pending, in_production, queue}]
          count          已造好（ready）数 —— 前端 xN 文本
          pending        建造中（not_ready）数 —— 前端红角标
          in_production  正在产单位的 ready 建筑数 —— 仅 tooltip
          queue          [{unit, progress}] 在产单位明细 —— 仅 tooltip

        ready 或 pending 任一 > 0 即纳入。调用方已有 try/except 保护。
        """
        bot = self._bot
        try:
            from sc2.ids.unit_typeid import UnitTypeId  # type: ignore[import]
        except ImportError:
            return []

        out: list[dict[str, Any]] = []
        for tid_name in self._PRODUCTION_BUILDING_NAMES:
            try:
                tid = UnitTypeId[tid_name]
            except KeyError:
                continue
            try:
                buildings = bot.structures(tid)  # type: ignore[union-attr]
                ready_n = buildings.ready.amount
                # 建造中（not_ready）= 已下基但未完工的建筑
                pending_n = buildings.not_ready.amount
            except Exception:
                continue
            # ready 或建造中任一 > 0 都要显示（修：原来 ready_n==0 跳过 → 第一个
            # 建筑还在盖时整行不显示，"建造中跟踪不到"的根因）
            if ready_n == 0 and pending_n == 0:
                continue

            # 在产进度：遍历每个就绪建筑的 orders（仅用于 tooltip，不再上角标）
            # 同时统计挂件(addon)状态：人族产能楼可挂科技实验室(TechLab)/反应堆(Reactor)。
            # 2026-06-17 用户：面板要能看出兵营/重工/机场是没挂件 / 挂科技 / 挂双倍。
            queue: list[dict[str, Any]] = []
            addon_none = addon_techlab = addon_reactor = 0
            try:
                for s in buildings.ready:
                    # 挂件分类（人族独有；神/虫 always none，无害）
                    if getattr(s, "has_reactor", False):
                        addon_reactor += 1
                    elif getattr(s, "has_techlab", False):
                        addon_techlab += 1
                    else:
                        addon_none += 1
                    if s.orders:
                        first_order = s.orders[0]
                        # ability 可能是 AbilityId 枚举或纯 int
                        ability_name = (
                            first_order.ability.name
                            if hasattr(first_order.ability, "name")
                            else str(first_order.ability)
                        )
                        queue.append(
                            {
                                "unit": ability_name,
                                "progress": int(first_order.progress * 100),
                            }
                        )
            except Exception:
                pass

            out.append(
                {
                    "building_id": tid.value,
                    "name_en": tid_name,
                    "name_zh": self._loc.structure(tid_name),
                    "count": ready_n,  # 已造好数（xN 文本显示）
                    "pending": pending_n,  # 建造中数（红角标显示）
                    "in_production": len(queue),  # 在产单位数（仅 tooltip）
                    "queue": queue,
                    # 挂件明细（ready 建筑里各挂件数）：none=没挂 / techlab=科技 / reactor=双倍。
                    # 前端按 techlab/reactor>0 渲染小标签（科技/双倍），全 none 则不显示标签。
                    "addons": {
                        "none": addon_none,
                        "techlab": addon_techlab,
                        "reactor": addon_reactor,
                    },
                }
            )

        return out

    def _build_army_units(self) -> list[dict[str, Any]]:
        """组装 army_units 列表（已有 + 建造中），供兵种行消费。

        返回格式：[{name_en, name_zh, count, pending}]
          count   = bot.units(tid).ready.amount（已造好）
          pending = bot.already_pending(tid)（在产+折跃中+morph 中，不含 ready）
          只纳入 count>0 或 pending>0 的兵种，按 _ARMY_UNIT_NAMES 顺序。
        """
        bot = self._bot
        try:
            from sc2.ids.unit_typeid import UnitTypeId
        except ImportError:
            return []

        out: list[dict[str, Any]] = []
        for tid_name in self._ARMY_UNIT_NAMES:
            try:
                tid = UnitTypeId[tid_name]
            except KeyError:
                continue
            try:
                count = bot.units(tid).ready.amount  # type: ignore[union-attr]
            except Exception:
                count = 0
            try:
                pending = bot.already_pending(tid)  # type: ignore[union-attr]
            except Exception:
                pending = 0
            if count == 0 and pending == 0:
                continue
            out.append(
                {
                    "name_en": tid_name,
                    "name_zh": self._loc.army_unit(tid_name),
                    "count": int(count),
                    "pending": int(pending),
                }
            )
        return out

    def _production_override_view(self, d: Directive) -> dict[str, Any]:
        """把一条 production override Directive 转成 snapshot 里的 view dict（P2）。

        字段：id / display / issued_at / status / status_reason(M3 加)。
        status 取值: pending / active / on_hold。PWA 卡片按此染色。
        """
        payload = d.payload
        display = self._format_production_override_display(payload)
        status_info = self._override_status.get(d.id, {})
        view: dict[str, Any] = {
            "id": d.id,
            "display": display,
            "issued_at": d.issued_at,
            "status": status_info.get("status", "pending"),
        }
        reason = status_info.get("reason", "")
        if reason:
            view["status_reason"] = reason
        return view

    def _format_production_override_display(self, payload: Any) -> str:
        """中文 display 格式（P2）：
        - PRODUCTION_OVERRIDE → '出 N <unit_zh>'（alias 翻译，无 alias 用英文）
        - TECH_OVERRIDE       → '研 <upgrade>'
        - EXPANSION_OVERRIDE  → '开 N 矿'
        - STRUCTURE_OVERRIDE  → '造 N <structure_type>[ @ <location_hint>]'
        """
        if isinstance(payload, ProductionOverridePayload):
            # 出兵恒增量语义(unit_count_built_since)→ "新增 N 个 X"。名字走 Localizer(lang-aware)
            parts = [
                _i18n_t(
                    "card.prodItem",
                    self._lang,
                    count=item.count,
                    unit=self._loc.unit(item.unit_type),
                )
                for item in payload.items
            ]
            return _i18n_t("card.prodAdd", self._lang, items=" / ".join(parts))
        if isinstance(payload, TechOverridePayload):
            return _i18n_t("card.research", self._lang, upg=self._loc.upgrade(payload.upgrade_id))
        if isinstance(payload, ExpansionOverridePayload):
            return _i18n_t("card.expand", self._lang, n=payload.target_count)
        if isinstance(payload, StructureOverridePayload):
            # 2026-06-02 用户:区分 新增(delta)/补齐(target),不统一。按 done_when kind 分:
            # built_since → "新增 N 个"(数新建成);structure_count → "补齐到 N 个"(数总数)。
            kindmap = self._structure_done_kind_map(getattr(payload, "done_when", None))
            parts = []
            for it in payload.items:
                loc = f" @ {it.location_hint}" if it.location_hint else ""
                kind, val = kindmap.get(it.structure_type.upper(), ("target", it.target_count or 0))
                key = "card.structAdd" if kind == "delta" else "card.structFill"
                parts.append(_i18n_t(key, self._lang, n=val, struct=it.structure_type, loc=loc))
            return " / ".join(parts)
        return _i18n_t("card.unknownOverride", self._lang)

    def _structure_done_kind_map(self, done_when: Any) -> dict[str, tuple[str, int]]:
        """从 structure_override 的 done_when 推每个建筑是 delta(新增) 还是 target(补齐)。

        structure_count_built_since → ("delta", N)(新增 N 个,数新建成);
        structure_count → ("target", N)(补齐到 N 个,数总数)。
        """
        out: dict[str, tuple[str, int]] = {}

        def _walk(node: Any) -> None:
            if node is None:
                return
            kind = getattr(node, "kind", None)
            if kind == "structure_count_built_since":
                out[str(getattr(node, "structure_type", "")).upper()] = (
                    "delta",
                    int(getattr(node, "value", 0)),
                )
            elif kind == "structure_count":
                out[str(getattr(node, "structure_type", "")).upper()] = (
                    "target",
                    int(getattr(node, "value", 0)),
                )
            elif kind in ("all_of", "any_of"):
                for c in getattr(node, "conditions", None) or []:
                    _walk(c)

        _walk(done_when)
        return out

    def _push_event(self, event_dict: dict[str, Any]) -> None:
        """推 event 帧（若 callback 已注入）。"""
        if self._event_callback is not None:
            self._event_callback(event_dict)

    # ------------------------------------------------------------------
    # 玩家话语入口
    # ------------------------------------------------------------------

    async def on_player_command(self, text: str, now: float) -> ParseOutcome:
        # 全量落 COMMANDS 流(2026-05-23 用户:识别失败 log 全量保留)。
        # 进 parser 前先记 user_text + ts,parse 完后再补一条带 outcome 的 record。
        # 这样即使 parser 抛异常,user_text 也保住了。
        with contextlib.suppress(Exception):
            self.session.log(
                LogStream.COMMANDS,
                {
                    "ts": now,
                    "phase": "received",
                    "user_text": text,
                },
            )
        ctx = self.build_parse_context(now)
        outcome = await self.parser.parse(text, ctx)

        # B 局内 memory:所有 outcome(含 ParseError) 都记进 _recent_commands +
        # 回填摘要。这样 LLM 下次 parse 看到的不仅是上次说了什么,还看到上次解出了什么。
        self._remember_command(text, now, outcome=outcome)

        # 全量落 COMMANDS 流(parse 完成,带 outcome 摘要)
        try:
            summary: dict[str, Any] = {
                "ts": now,
                "phase": "parsed",
                "user_text": text,
                "outcome_kind": type(outcome).__name__,
            }
            if isinstance(outcome, IntentParseResult):
                summary["directive_count"] = len(outcome.directives)
                summary["interpretation_zh"] = outcome.interpretation_zh or ""
            elif isinstance(outcome, ParseError):
                summary["error_kind"] = outcome.kind.value
                summary["error_message"] = outcome.message
            elif isinstance(outcome, AmbiguousParse):
                summary["candidate_count"] = len(outcome.candidates)
            self.session.log(LogStream.COMMANDS, summary)
        except Exception:
            # log 失败不阻塞主流程
            pass

        if isinstance(outcome, IntentParseResult):
            self._inject_camera_point(outcome.directives, ctx.camera_point)
            self._inject_camera_selectors(outcome.directives, ctx.camera_point)
            self._submit_directives(outcome.directives, now)
        elif isinstance(outcome, AmbiguousParse):
            # 暂不 submit；UI 层等玩家二次确认后再 confirm_ambiguous
            pass
        elif isinstance(outcome, ClarificationRequest):
            # 2026-05-24:LLM 给玩家选项 → 推 snapshot 让 PWA 弹层。
            # 玩家点选 → submit_clarification_choice;点 × → cancel_clarification。
            self._pending_clarification = outcome
            with contextlib.suppress(Exception):
                self._push_snapshot(now)
        elif isinstance(outcome, ParseError):
            self.session.log_event(
                Event(
                    ts=now,
                    kind=EventKind.DIRECTIVE_FAILED,
                    payload={
                        "user_text": text,
                        "error_kind": outcome.kind.value,
                        "error_message": outcome.message,
                    },
                    priority="low",
                    caused_by=f"voice:{text[:30]}",
                )
            )

        return outcome

    def confirm_ambiguous(self, ambiguous: AmbiguousParse, now: float, accepted: bool) -> None:
        """玩家二次确认 ambiguous parse。"""
        if accepted:
            self._submit_directives(ambiguous.result.directives, now)

    def _maybe_attach_task_monitor(self, submitted: Directive) -> None:
        """P3.2: done_when 非空时把 directive 注册到 task_monitor。

        L4 production-type 永远不带 timeout_s（"造 N 个 X" 的完成条件就是个数，
        LLM 偶尔会写 timeout_s=60 当兜底，但前期单位/科技 60s 造不完就误删；
        没 done_when 时（"持续出叉子"）也不会 attach，自然只能手动 ×。
        """
        if self.task_monitor is None:
            return
        dw = submitted.payload.done_when
        if dw is None:
            return
        from pydantic import BaseModel

        done_when_dict: dict[str, Any] = dw.model_dump() if isinstance(dw, BaseModel) else {}

        timeout_s = submitted.payload.timeout_s
        if submitted.type in {
            DirectiveType.PRODUCTION_OVERRIDE,
            DirectiveType.TECH_OVERRIDE,
            DirectiveType.EXPANSION_OVERRIDE,
            DirectiveType.STRUCTURE_OVERRIDE,
        }:
            timeout_s = None  # L4 production 不要时间兜底

        # 2026-05-24 用户:unit_arrived/unit_held_position checker 需 selector tags。
        # 此时 _standing_order_tags 已由 _claim_directive_units 填好(MOVE/SCOUT/
        # UNIT_CLAIM ephemeral)或 _assign_standing_order_units(persistent)。
        unit_tags = self._standing_order_tags.get(submitted.id) or None
        self.task_monitor.attach_directive(
            directive_id=submitted.id,
            done_when=done_when_dict,
            issued_at=submitted.issued_at,
            timeout_s=timeout_s,
            unit_tags=unit_tags,
        )

    def submit_directive(self, directive: Directive, now: float) -> None:
        """单条 directive 便捷入口（UI 按钮 / 子进程 down_q handler 调用）。

        直接委托 _submit_directives，保持相同路由逻辑（strategy 时机检测 / standing /
        production_overrides / _in_flight 分流）。
        """
        self._submit_directives([directive], now)

    def _dedupe_directives(self, directives: list[Directive]) -> list[Directive]:
        """2026-05-28 用户:同 batch 内 LLM 误 emit 多条重复 directive 去重。

        典型场景:LLM 解析"升级 1 防"emit 两条 tech_override —
          {upgrade_id:"ProtossGroundArmorsLevel1"} +
          {upgrade_id:"PROTOSSGROUNDARMORSLEVEL1"}
        本质同一升级,大小写不同。两条都进 board → 两张卡片 + 双倍 task_monitor
        浪费 + 玩家看到重复。

        dedupe key 按 directive type:
          tech_override → upgrade_id.upper()
          structure_override → tuple(items.structure_type.upper())
          production_override → tuple(items.unit_type.upper())
          expansion_override → target_count
          其他类型不去重(tactical_objective 可能玩家本来就要多条)。

        保留第一条,后续重复 silent drop。
        """
        seen_keys: set[tuple] = set()
        out: list[Directive] = []
        for d in directives:
            t = d.type
            key: tuple | None = None
            try:
                if t == DirectiveType.TECH_OVERRIDE:
                    upgrade_id = str(getattr(d.payload, "upgrade_id", "") or "")
                    key = (t.value, upgrade_id.upper())
                elif t == DirectiveType.STRUCTURE_OVERRIDE:
                    items = getattr(d.payload, "items", []) or []
                    key = (t.value, tuple(sorted(str(it.structure_type).upper() for it in items)))
                elif t == DirectiveType.PRODUCTION_OVERRIDE:
                    items = getattr(d.payload, "items", []) or []
                    key = (t.value, tuple(sorted(str(it.unit_type).upper() for it in items)))
                elif t == DirectiveType.EXPANSION_OVERRIDE:
                    key = (t.value, int(getattr(d.payload, "target_count", 0)))
            except Exception:
                key = None
            if key is not None and key in seen_keys:
                logger.warning(
                    "dedupe duplicate directive (%s) key=%s — drop %s",
                    t.value,
                    key,
                    d.id[:8],
                )
                continue
            if key is not None:
                seen_keys.add(key)
            out.append(d)
        return out

    def _resolve_structure_delta(self, d: Directive) -> Directive:
        """2026-05-28 用户:structure_override 的 delta 语义解算。

        玩家"补一个 BF"(delta=1)在 LLM 层不看当前数,直接 emit delta=1。
        submit 时 Director 用当前 ready_count 算 target_count = ready + delta,
        然后清掉 delta(传给后端的就是普通 target_count 语义,_exec_structure_override
        不变)。

        非 STRUCTURE_OVERRIDE / 没 delta item 的 directive 原样返。
        """
        if d.type != DirectiveType.STRUCTURE_OVERRIDE:
            return d
        payload = d.payload
        items = getattr(payload, "items", None)
        if not items or not any(getattr(it, "delta", None) is not None for it in items):
            return d
        from vibecraft.directives.models import (
            AllOf,
            StructureCount,
            StructureCountBuiltSince,
        )

        new_items: list[StructureItem] = []
        # 2026-06-02 实测 bug:delta 项的 done_when 被 LLM 设成 count>=delta(把新增当
        # 绝对总数),delta 解算只改 target_count 没改 done_when → 已有 ≥delta 个时
        # count>=delta 立刻满足 → directive 秒 done 不建造("补一个by"已有1个 → done)。
        # 且就算改成 count>=ready+delta(绝对),建造中原建筑被打掉也会过建/卡死。
        # 正确:含 delta 项时按 item 重建 done_when —— delta 项数"新建成个数"
        # (structure_count_built_since,损毁免疫,建够 delta 个就停);target 项数总数
        # (structure_count,没到上限一直补,被打掉会重建)。
        conditions: list[Any] = []
        for it in items:
            if it.delta is None:
                new_items.append(it)
                conditions.append(
                    StructureCount(
                        kind="structure_count",
                        structure_type=it.structure_type,
                        op=">=",
                        value=int(it.target_count or 1),
                    )
                )
                continue
            type_name = it.structure_type.upper()
            ready_count = 0
            try:
                from sc2.ids.unit_typeid import UnitTypeId

                type_id = UnitTypeId[type_name]
                # 必须用 _count_equivalent(数同质化升级体),不能用 structures(type_id).ready —
                # 2026-06-08 踩坑:中后期 BG 全升 WARPGATE 后 structures(GATEWAY).ready=0
                # → target=0+delta;但执行层 _exec_structure_override 用 _count_equivalent 数到
                # 全部 WG ≥ target → 秒判 structure_done 一个没造。两处计数口径必须一致。
                # (同类:Zerg HATCHERY→LAIR/HIVE。见 _count_equivalent 文档。)
                ready_count, _ = self._count_equivalent(type_id)
            except Exception as exc:
                logger.debug("delta resolve fail for %s: %s", type_name, exc)
            resolved_target = ready_count + it.delta
            logger.info(
                "structure_delta resolved: %s delta=%d + ready=%d → target=%d "
                "(done_when=built_since>=%d)",
                type_name,
                it.delta,
                ready_count,
                resolved_target,
                it.delta,
            )
            new_items.append(
                it.model_copy(
                    update={
                        "target_count": resolved_target,
                        "delta": None,
                    }
                )
            )
            conditions.append(
                StructureCountBuiltSince(
                    kind="structure_count_built_since",
                    structure_type=it.structure_type,
                    op=">=",
                    value=int(it.delta),
                )
            )
        new_done_when = (
            conditions[0] if len(conditions) == 1 else AllOf(kind="all_of", conditions=conditions)
        )
        new_payload = payload.model_copy(update={"items": new_items, "done_when": new_done_when})
        return d.model_copy(update={"payload": new_payload})

    def _maybe_build_townhall_confirm(
        self, directives: list[Directive]
    ) -> ClarificationRequest | None:
        """建 townhall(by_probe)落点 8-13 格模糊 → 构造"修正到矿区/就在原地"二选一确认。

        2026-06-09 用户:玩家"在这开矿"指定点离最近 expansion 在 (SNAP, CONFIRM] 区间
        (8~13 格)时,既可能是"想框那片矿、点偏了",也可能是"故意造偏挡路",不擅自决定 →
        弹 clarification 让玩家选。≤8 静默 snap、>13 静默原地(都不进这里,走 facade)。

        命中返回 ClarificationRequest(两选项各带一份调整过 point 的 directive 批);
        没有模糊 townhall build_at 返回 None。**复用 _pending_clarification 通道**——
        前端 ClarificationOverlay / confirm_clarification 帧 / submit_clarification_choice
        全现成,零前端改动。选项 build_at 带 placement_confirmed=True 防二次拦截。
        """
        from vibecraft.bot.named_spot import (
            TOWNHALL_CONFIRM_MAX_DIST,
            TOWNHALL_SNAP_MAX_DIST,
            TOWNHALL_TYPE_NAMES,
            closest_expansion_location,
        )

        bot = self._bot
        if bot is None:
            return None
        from sc2.position import Point2

        for idx, d in enumerate(directives):
            payload = d.payload
            if not isinstance(payload, BuildAtPayload):
                continue
            if not payload.by_probe or payload.point is None:
                continue
            if payload.placement_confirmed:
                continue
            if payload.structure_type.upper() not in TOWNHALL_TYPE_NAMES:
                continue
            nearest = closest_expansion_location(payload.point, bot)
            if nearest is None:
                continue
            d_exp = Point2(payload.point).distance_to(nearest)
            if not (TOWNHALL_SNAP_MAX_DIST < d_exp <= TOWNHALL_CONFIRM_MAX_DIST):
                continue  # ≤8 snap / >13 原地,都不弹

            snapped_pt = (float(nearest.x), float(nearest.y))
            orig_pt = (float(payload.point[0]), float(payload.point[1]))
            opt_snap = self._clone_batch_with_build_point(directives, idx, snapped_pt)
            opt_orig = self._clone_batch_with_build_point(directives, idx, orig_pt)
            lang = self._lang
            return ClarificationRequest(
                question=_i18n_t("clarify.townhall.question", lang, dist=f"{d_exp:.0f}"),
                options=[
                    ClarificationOption(
                        label=_i18n_t("clarify.townhall.labelSnap", lang),
                        interpretation_zh=_i18n_t("clarify.townhall.interpSnap", lang),
                        directives=opt_snap,
                    ),
                    ClarificationOption(
                        label=_i18n_t("clarify.townhall.labelKeep", lang),
                        interpretation_zh=_i18n_t("clarify.townhall.interpKeep", lang),
                        directives=opt_orig,
                    ),
                ],
                source_text=d.source_text or "",
            )
        return None

    def _clone_batch_with_build_point(
        self, directives: list[Directive], idx: int, point: tuple[float, float]
    ) -> list[Directive]:
        """复制整批 directive,把第 idx 条(build_at)的 point 改成 point + 标 placement_confirmed。

        其余 directive 原样保留(claim 等),整批作为某 clarification 选项的动作。
        """
        out: list[Directive] = []
        for i, d in enumerate(directives):
            if i == idx:
                new_payload = d.payload.model_copy(
                    update={"point": point, "placement_confirmed": True}
                )
                out.append(d.model_copy(update={"payload": new_payload}))
            else:
                out.append(d)
        return out

    def _recommend_addon_mix(self, building_type: str, count: int) -> tuple[int, int]:
        """推荐 (techlab_n, reactor_n) 挂件分配方案(P1 addon decision).

        算法(§5 定稿):
        1. 从活跃 ProductionOverride overlays 抽出该楼能生产的兵种。
        2. 用 _unit_requires_techlab 数出需要 TechLab 的不同兵种数 = techlab_need。
        3. 若无法拿到活跃兵种(SC2 不可用/无 overlay),退回默认:兵营=1,重工=1,机场=0。
        4. 减去场上已有同类 TechLab 挂件(增量;§5 "减去已有挂件")——
           已有 techlab 已满足部分/全部需科技兵种,新楼只需补差额。
        5. clamp 到 [1 if techlab_need>0 else 0, count]。
        6. reactor = count - techlab。
        7. 有 _ADDON_REACTOR_UNITS 兵种在需求集且 reactor 有余量 → reactor >= 1。
        返回 (techlab_n, reactor_n),均 >= 0 且和 <= count。
        """
        building_upper = building_type.upper()
        default_techlab = {"BARRACKS": 1, "FACTORY": 1, "STARPORT": 0}.get(building_upper, 0)

        techlab_need = default_techlab
        has_reactor_unit = False

        try:
            from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM  # type: ignore[import]
            from sc2.ids.unit_typeid import UnitTypeId  # type: ignore[import]

            parent_id = UnitTypeId[building_upper]
            # 该楼可以生产的所有兵种
            building_units: set[Any] = {
                uid for uid, parents in UNIT_TRAINED_FROM.items() if parent_id in parents
            }

            # 活跃 ProductionOverride 里的兵种(取 UnitTypeId)
            active_units: set[Any] = set()
            for d in self.board.overlays:
                if isinstance(d.payload, ProductionOverridePayload):
                    for item in d.payload.items:
                        with contextlib.suppress(KeyError):
                            active_units.add(UnitTypeId[item.unit_type.upper()])

            reactor_unit_ids = {
                UnitTypeId[n] for n in self._ADDON_REACTOR_UNITS if n in UnitTypeId.__members__
            }
            if active_units:
                # 有活跃出兵指令 → 需求驱动:取"活跃 ∩ 该楼能出"里需科技的不同兵种数,
                # 但**保底 default_techlab**(兵营/重工的兴奋剂/护盾/升级在 techlab 研究,纯枪兵也要 1)。
                relevant = active_units & building_units
                techlab_units = {u for u in relevant if self._unit_requires_techlab(u)}
                techlab_need = max(len(techlab_units), default_techlab)
                has_reactor_unit = bool(relevant & reactor_unit_ids) or not relevant
            else:
                # 无活跃出兵指令 → 无需求信号 → 用 per-type 合理默认(兵营1/重工1/机场0),
                # **不**展开成"该楼所有兵种"(否则机场会算成全科技);预留 reactor。
                techlab_need = default_techlab
                has_reactor_unit = True
        except Exception:
            pass  # SC2 不可用或解析失败,用 default_techlab

        # 减去场上已有同类 TechLab 挂件(增量推荐;§5 "减去已有挂件"):
        # 场上已有的 techlab 已能满足部分/全部需科技兵种 → 新楼只需补差额。
        # 已有数 >= 需求 → 减到 <=0 → 下面 clamp 归 0 → 新楼全挂 reactor。
        techlab_addon_name = {
            "BARRACKS": "BARRACKSTECHLAB",
            "FACTORY": "FACTORYTECHLAB",
            "STARPORT": "STARPORTTECHLAB",
        }.get(building_upper)
        if techlab_addon_name and self._bot is not None:
            try:
                from sc2.ids.unit_typeid import UnitTypeId  # type: ignore[import]

                existing_techlab = int(
                    self._bot.structures(UnitTypeId[techlab_addon_name]).amount  # type: ignore[union-attr]
                )
                techlab_need -= existing_techlab
            except Exception:
                pass  # 拿不到场上 structures → 减 0,不报错

        # clamp [≥1 if need else 0, count];减完可能 <=0 → 全 reactor
        techlab_need = max(1 if techlab_need > 0 else 0, min(techlab_need, count))
        reactor = count - techlab_need

        # mass-mineral 兵存在且 reactor 有余量 → reactor >= 1
        if has_reactor_unit and reactor == 0 and count > 1:
            techlab_need = count - 1
            reactor = 1

        return (max(0, techlab_need), max(0, reactor))

    def _maybe_build_addon_confirm(
        self, directives: list[Directive]
    ) -> ClarificationRequest | None:
        """VOICE 指令里有产能建筑(兵营/重工/机场)且 addon_decided=False → 弹挂件 3 选项.

        复用 _pending_clarification 通道(仿 _maybe_build_townhall_confirm)。
        gate:
          - race != terran → return None
          - issued_by != VOICE → return None
          - 只对 addon_decided=False 的产能建筑条目触发
        单次只弹第一个命中的条目(一句话多种产能楼分多次说)。
        2026-06-18 P1 addon decision。
        """
        from vibecraft.directives.types import IssuedBy

        my_race = (getattr(self.parser, "my_race", None) or "").lower()
        if my_race and my_race != "terran":
            return None

        production_buildings = {"BARRACKS", "FACTORY", "STARPORT"}

        for d_idx, d in enumerate(directives):
            if d.issued_by != IssuedBy.VOICE:
                continue
            payload = d.payload
            if not isinstance(payload, StructureOverridePayload):
                continue
            for item_idx, item in enumerate(payload.items):
                if item.structure_type.upper() not in production_buildings:
                    continue
                if item.addon_decided:
                    continue

                building_upper = item.structure_type.upper()
                count = item.delta or item.target_count or 1
                techlab_n, reactor_n = self._recommend_addon_mix(building_upper, count)
                # 澄清问句用建筑「全名」（兵营/重工/机场），不用 hotkey（自然语言语境更清楚；
                # 保持 i18n 重构前 zh 行为）。全名表未命中 → 回退 Localizer.structure（hotkey）。
                _full_key = f"structFull.{building_upper}"
                building_zh = _i18n_t(_full_key, self._lang)
                if building_zh == _full_key:
                    building_zh = self._loc.structure(building_upper) or item.structure_type
                techlab_name, reactor_name = self._ADDON_PAIR.get(
                    building_upper, ("TechLab", "Reactor")
                )

                # 选项 a:不挂(addon_decided=True,无挂件 item)
                batch_a = self._clone_batch_for_addon_option(
                    directives, d_idx, item_idx, addon_items=[]
                )
                # 选项 b:推荐
                addon_items_b: list[StructureItem] = []
                if techlab_n > 0:
                    addon_items_b.append(
                        StructureItem(structure_type=techlab_name, delta=techlab_n)
                    )
                if reactor_n > 0:
                    addon_items_b.append(
                        StructureItem(structure_type=reactor_name, delta=reactor_n)
                    )
                batch_b = self._clone_batch_for_addon_option(
                    directives, d_idx, item_idx, addon_items=addon_items_b
                )
                # 选项 c:取消(空批)
                batch_c: list[Directive] = []

                lang = self._lang
                return ClarificationRequest(
                    question=_i18n_t(
                        "clarify.addon.question", lang, count=count, building=building_zh
                    ),
                    options=[
                        ClarificationOption(
                            label=_i18n_t("clarify.addon.labelNone", lang),
                            interpretation_zh=_i18n_t(
                                "clarify.addon.interpNone", lang, count=count, building=building_zh
                            ),
                            directives=batch_a,
                        ),
                        ClarificationOption(
                            label=_i18n_t(
                                "clarify.addon.labelRecommend",
                                lang,
                                techlab=techlab_n,
                                reactor=reactor_n,
                            ),
                            interpretation_zh=_i18n_t(
                                "clarify.addon.interpRecommend",
                                lang,
                                techlab=techlab_n,
                                reactor=reactor_n,
                                building=building_zh,
                            ),
                            directives=batch_b,
                        ),
                        ClarificationOption(
                            label=_i18n_t("clarify.cancel", lang),
                            interpretation_zh=_i18n_t("clarify.addon.interpCancel", lang),
                            directives=batch_c,
                        ),
                    ],
                    source_text=d.source_text or "",
                )
        return None

    def _clone_batch_for_addon_option(
        self,
        directives: list[Directive],
        target_idx: int,
        item_idx: int,
        addon_items: list[StructureItem],
    ) -> list[Directive]:
        """复制整批 directive,把第 target_idx 条 StructureOverride 里第 item_idx 个 item
        的 addon_decided 改 True,并在该 item 之后插入 addon_items(可为空)。
        """
        out: list[Directive] = []
        for i, d in enumerate(directives):
            if i == target_idx:
                assert isinstance(d.payload, StructureOverridePayload)
                new_items: list[StructureItem] = []
                for j, item in enumerate(d.payload.items):
                    if j == item_idx:
                        new_items.append(item.model_copy(update={"addon_decided": True}))
                    else:
                        new_items.append(item)
                new_items.extend(addon_items)
                new_payload = d.payload.model_copy(update={"items": new_items})
                out.append(d.model_copy(update={"payload": new_payload}))
            else:
                out.append(d)
        return out

    def _submit_directives(self, directives: list[Directive], now: float) -> None:
        from vibecraft.directives.types import IssuedBy

        # 2026-05-28 用户:同 batch 内重复的 tech_override(LLM 偶尔 emit 两条
        # 同 upgrade 仅大小写不同的 directive,如 ProtossGroundArmorsLevel1 +
        # PROTOSSGROUNDARMORSLEVEL1)→ 去重保留第一条。同样 dedupe
        # expansion_override(同 target_count 重复)。
        directives = self._dedupe_directives(directives)

        # 2026-06-01 用户(方案3):同一句话里 UNIT_RELEASE 先于其它(尤其 UNIT_CLAIM)处理。
        # "探路农民回来吧，去占瞭望塔" = release(scout) + claim(watchtower)。release 必须先跑,
        # 这样它执行时同句 claim 还没生效、被 claim 的 probe 还没进"移动中"态 → 泛化 release
        # 的 selector 抓不到它 → 不会把刚 claim 去瞭望塔的农民又释放回采矿。stable sort 只把
        # release 提前,其余相对顺序不变。
        directives = sorted(
            directives, key=lambda d: 0 if d.type == DirectiveType.UNIT_RELEASE else 1
        )

        # 2026-06-18 P1 挂件决策诊断(env 门控):打 structure_override items + addon_decided,
        # 真局自验时 grep ADDONTRACE 确认 LLM 解析的挂件分配 + 弹窗是否该触发。
        import os as _os

        if _os.environ.get("VIBECRAFT_ADDON_TRACE"):
            for _d in directives:
                if isinstance(_d.payload, StructureOverridePayload):
                    _items = ",".join(
                        f"{_it.structure_type}x{_it.delta or _it.target_count}"
                        f"(dec={getattr(_it, 'addon_decided', '?')})"
                        for _it in _d.payload.items
                    )
                    logger.info("ADDONTRACE src=%s items=%s", _d.issued_by.value, _items)

        # 2026-06-09 用户:建 townhall 落点 8-13 格模糊 → 弹确认(修正到矿区/就在原地),
        # 不擅自决定。命中则 hold 整批、推 clarification,玩家选完再 submit 对应变体。
        confirm = self._maybe_build_townhall_confirm(directives)
        if confirm is not None:
            self._pending_clarification = confirm
            logger.info("townhall 落点模糊,弹确认: %s", confirm.question)
            self._push_snapshot(now)
            return

        # 2026-06-18 P1 addon decision:VOICE 产能建筑(兵营/重工/机场)且 addon_decided=False
        # → 弹挂件 3 选项。单槽互斥:townhall confirm 已命中则本帧让位。
        if self._pending_clarification is None:
            addon_confirm = self._maybe_build_addon_confirm(directives)
            if addon_confirm is not None:
                self._pending_clarification = addon_confirm
                logger.info("addon 挂件未决定,弹确认: %s", addon_confirm.question)
                self._push_snapshot(now)
                return

        for d in directives:
            d_with_ts = d.model_copy(update={"issued_at": now})
            # 2026-05-28 用户:structure_override delta 语义("补 N 个"= 新增 N,
            # 不看当前)。submit 时用当前 ready_count 解算成绝对 target_count。
            d_with_ts = self._resolve_structure_delta(d_with_ts)
            if (
                d_with_ts.type == DirectiveType.STRATEGY_SET
                and d_with_ts.issued_by == IssuedBy.VOICE
            ):
                # VOICE 切剧本前先检测时机;过期 → 拦下来等玩家硬转确认
                reasons = self._check_strategy_obsolete(d_with_ts)
                if reasons:
                    self._pending_force_strategy = (d_with_ts, reasons)
                    self._push_snapshot(now)
                    continue
                submitted = self.board.submit(d_with_ts, now=now)
                self._log_directive(
                    "submitted", submitted, now, effective_at=submitted.effective_at
                )
                self._in_flight[submitted.id] = submitted
                # P3.2: 注册到 task_monitor
                self._maybe_attach_task_monitor(submitted)
            else:
                submitted = self.board.submit(d_with_ts, now=now)
                self._log_directive(
                    "submitted", submitted, now, effective_at=submitted.effective_at
                )
                # 2026-06-13 Task #523：跨族校验——指令种族 ≠ 玩家种族时友好拒绝，continue 跳路由。
                if self._reject_if_cross_race(submitted):
                    continue
                # 2026-06-19 Task #558：build 类 structure_type 必须是建筑，非建筑（如大舰）→ 拒绝。
                if self._reject_if_invalid_structure_type(submitted):
                    continue
                # P1.2: persistent=True 的 unit_claim 进 standing_orders，不进 _in_flight
                # 2026-06-13 持续征兵：LLM 给 recruit_new=True 但 persistent=False → 自动升级为 persistent
                if (
                    isinstance(submitted.payload, UnitClaimPayload)
                    and submitted.payload.recruit_new
                    and not submitted.payload.persistent
                ):
                    submitted.payload = submitted.payload.model_copy(update={"persistent": True})
                if isinstance(submitted.payload, UnitClaimPayload) and submitted.payload.persistent:
                    # 2026-06-29 #580: group_harass 幂等更新 —— 若已有同 verb claim 则更新它、不新建。
                    if not self._try_upsert_group_harass(submitted, now):
                        self.standing_orders.append(submitted)
                        # 2026-06-06 修复:带 activate_when 的 persistent claim 必须**先过激活门**。
                        # 之前直接 _assign_standing_order_units 立即执行 → 链式第二步(等农民到 A 再
                        # 去 B)在提交时就发了"去 B",被第一步"去 A"覆盖丢掉 → 农民到 A 后干站不去 B。
                        # 改:activate_when 未满足 → 挂 _pending_activation,每 tick 重查,满足才 assign。
                        _aw = getattr(submitted.payload, "activate_when", None)
                        if _aw is not None and not self._is_activation_satisfied(_aw, submitted):
                            self._pending_activation[submitted.id] = submitted
                            self._set_override_status(
                                submitted, "waiting", _i18n_t("strategy.waitActivation", self._lang)
                            )
                        else:
                            # P5.E: 立即 resolve selector + 让 sharpy 让位（set_unit_role）
                            self._assign_standing_order_units(submitted)
                # P2: L4 production/tech/expansion/structure/drop_act override 进 production_overrides
                elif submitted.type in (
                    DirectiveType.PRODUCTION_OVERRIDE,
                    DirectiveType.TECH_OVERRIDE,
                    DirectiveType.EXPANSION_OVERRIDE,
                    DirectiveType.STRUCTURE_OVERRIDE,
                    DirectiveType.DROP_ACT,
                ):
                    self.production_overrides.append(submitted)
                    # M3: L4 wire — emit "已加入生产队列" event 给 PWA(玩家反馈)
                    self._emit_production_queued_event(submitted, now)
                # 2026-05-30 view_follow / production_block：persistent 状态 directive，
                # 不走 standing_orders(没 selector/task)，也不走 production_overrides。
                # 直接进 _in_flight，由 _apply_to_facade 立即执行 facade 调用。
                # 2026-06-10 stealth_mine：同样是 persistent 状态 directive（StealthCellManager
                # 接管生命周期），_apply_to_facade 里调 create_cell，on_tick 驱动状态机。
                elif submitted.type in (
                    DirectiveType.VIEW_FOLLOW,
                    DirectiveType.PRODUCTION_BLOCK,
                    DirectiveType.RALLY_POINT,  # 2026-06-07 出兵集结点:persistent 全局态
                    DirectiveType.STEALTH_MINE,  # 2026-06-10 偷矿:StealthCellManager 接管
                ):
                    self._in_flight[submitted.id] = submitted
                elif submitted.type == DirectiveType.UNIT_RELEASE:
                    # 2026-06-01 用户(方案3):release 的整体效果提前到 submit 执行(像
                    # persistent claim),不再延迟到 commit。配合上面 release-先排序,同句
                    # claim 还没生效 → 泛化 release 抓不到被 claim 的 probe → 不再误伤。
                    # 仍进 _in_flight 走正常 board 生命周期(commit → done),但 commit 的
                    # _apply_to_facade 只 mark done、不重复执行效果(见该处)。
                    self._in_flight[submitted.id] = submitted
                    self._claim_directive_units(submitted)  # release_unit_role(还给 sharpy)
                    assert isinstance(submitted.payload, UnitReleasePayload)
                    self._apply_unit_release(submitted.payload)  # set IDLE/ARMY + cancel scout
                elif submitted.type == DirectiveType.GROUP_ASSIGN:
                    self._in_flight[submitted.id] = submitted
                    self._apply_group_assign(submitted)
                    assert isinstance(submitted.payload, GroupAssignPayload)
                    if submitted.payload.auto_enroll:
                        # 2026-06-13 持续征兵：留在 _in_flight，注册 watcher。
                        # seen 初始化为当前所有匹配 unit_type 的 tags（全量，
                        # 不能只用 SET 进队的截断结果，否则旧单位会被误当"新出的"）。
                        ut = submitted.payload.selector.unit_type or ""
                        seen_now: set[int] = set(
                            self.facade.resolve_selector(unit_type=ut) if ut else []
                        )
                        self._recruit_watchers[submitted.id] = {
                            "kind": "group",
                            "group_id": submitted.payload.group_id,
                            "unit_type": ut,
                            "seen": seen_now,
                        }
                        logger.info(
                            "RECRUIT register group=%d unit_type=%s seen=%d id=%s",
                            submitted.payload.group_id,
                            ut,
                            len(seen_now),
                            submitted.id[:8],
                        )
                    else:
                        # 普通编队：submit 即执行，立即 done。
                        self._release_directive_done(submitted, now, reason="group_assigned")
                elif submitted.type == DirectiveType.GROUP_CLEAR:
                    # 2026-06-01 语音编队解散：submit 即执行，立即 done。
                    self._in_flight[submitted.id] = submitted
                    self._apply_group_clear(submitted)
                    self._release_directive_done(submitted, now, reason="group_cleared")
                else:
                    self._in_flight[submitted.id] = submitted
                    # 2026-05-24 用户:被指令单位 set Reserved 防 sharpy 派别的。
                    # MOVE/SCOUT/UNIT_CLAIM ephemeral 都走这里。
                    self._claim_directive_units(submitted)
                # P3.2: 注册到 task_monitor（有 done_when 时才有意义，但 attach 本身 None-safe）
                self._maybe_attach_task_monitor(submitted)
        # 2026-05-25 bug A 修复(配套):玩家动作即时反馈。submit 后立即推 snapshot,
        # TacticsButton 等 UI 不用等 2s 兜底周期才看到 active_tactics +1。
        if directives:
            self._push_snapshot(now)

    def _log_directive(self, event: str, d: Directive, now: float, **extra: object) -> None:
        """向 directives.jsonl 写一行 directive 生命周期记录。

        event: "submitted" / "committed" / "released" / "revoked" 等
        d: 对应的 Directive 对象
        extra: 附加字段（effective_at / reason 等）
        """
        record: dict[str, object] = {
            "ts": round(now, 3),
            "event": event,
            "directive_id": d.id,
            "type": d.type.value,
            "issued_by": d.issued_by.value,
            "issued_at": d.issued_at,
            "source_text": d.source_text or "",
            **extra,
        }
        self.session.log(LogStream.DIRECTIVES, record)

    def _resolve_selector_with_count(self, sel: Any | None) -> list[int]:
        """2026-05-25 bug 4:统一 selector → tags 解析,**按 sel.count 截断**。

        Selector 协议:facade.resolve_selector(unit_type, tag, tags) 返回 ALL
        匹配 tags(不知道 count)。每个 caller 都得自己 cap,否则 LLM "一个农民"
        + count=1 会派全军农民(用户报"探路农民去对方家 → 所有农民被拉走")。

        以前 _assign_standing_order_units / _claim_directive_units 单独 cap,
        但 _apply_unit_claim / _apply_to_facade.MOVE 漏 cap → bug 4。
        统一走本 helper 消除 spec 漂移。

        sel=None → 返 []。
        """
        if sel is None:
            return []
        # 2026-06-01 语音编队：group_id → 直接查 _voice_groups，不走 facade.resolve_selector。
        group_id = getattr(sel, "group_id", None)
        if group_id is not None:
            tags = list(self._voice_groups.get(group_id, set()))
            tags = self._filter_by_unit_state(tags, sel)  # WP-B: 先状态过滤再 count 截断
            count = getattr(sel, "count", None)
            if count is not None and count > 0:
                tags = tags[:count]
            return tags
        # 2026-06-02 连续指令任务链：chain_id 已绑 → 返回绑定的同一单位；未绑（第一步）→
        # 用 selector 其它字段解析 + 绑定 chain_id。让同一农民接力走完整条链。
        chain_id = getattr(sel, "chain_id", None)
        if chain_id is not None:
            bound = self._task_chains.get(chain_id)
            if bound:
                tags = list(bound)
                tags = self._filter_by_unit_state(tags, sel)  # WP-B: 先状态过滤再 count 截断
                count = getattr(sel, "count", None)
                if count is not None and count > 0:
                    tags = tags[:count]
                return tags
            # 第一步：解析具体 selector，绑定到 chain_id
            tags = (
                self.facade.resolve_selector(unit_type=sel.unit_type, tag=sel.tag, tags=sel.tags)
                or []
            )
            tags = self._filter_by_unit_state(tags, sel)  # WP-B: 先状态过滤再 count 截断
            count = getattr(sel, "count", None)
            if count is not None and count > 0:
                tags = tags[:count]
            if tags:
                self._task_chains[chain_id] = set(tags)
                self._chain_structures.pop(chain_id, None)  # 新链:清旧的建筑 tag 残留
            return tags
        # 2026-06-03 用户:语意重选 —— "守瞭望塔的追猎X" 这类"重选一个正在执行任务的单位"。
        # 指派时把单位的识别语意（守的地点 named_spot / 任务 verb / 类型）记进 _unit_semantics，
        # 这里按 assigned_spot(地点) / primary_verb_prefix(任务) + unit_type 匹配回那个 tag。
        # （resolve_selector 只认 unit_type/tag/tags，认不出"守塔的那个"，正是崩溃根因。）
        if getattr(sel, "assigned_spot", None) or getattr(sel, "primary_verb_prefix", None):
            tags = self._resolve_by_semantics(sel)
            tags = self._filter_by_unit_state(tags, sel)  # WP-B: 先状态过滤再 count 截断
            count = getattr(sel, "count", None)
            if count is not None and count > 0:
                tags = tags[:count]
            return tags
        tags = (
            self.facade.resolve_selector(unit_type=sel.unit_type, tag=sel.tag, tags=sel.tags) or []
        )
        tags = self._filter_by_unit_state(tags, sel)  # WP-B: 先状态过滤再 count 截断
        tags = self._sort_by_position(tags, sel)  # 2026-06-08 "前线/后面那个"按实际位置排
        count = getattr(sel, "count", None)
        if count is not None and count > 0:
            tags = tags[:count]
        return tags

    def _sort_by_position(self, tags: list[int], sel: Any) -> list[int]:
        """2026-06-08 用户(P2):position=forward/back → 按单位**当前实际位置**离敌方主基地
        远近排序。forward=最靠前(离敌近)在前,back=最靠后(离敌远)在前。配 count 取最前/后 N 个。
        解决"前线那个追猎撤退"选不到(它在前线是物理位置,不是被指派去 forward)。
        """
        pos = getattr(sel, "position", None)
        if pos not in ("forward", "back"):
            return tags
        bot = self._bot
        if bot is None or not tags:
            return tags
        try:
            enemy = bot.enemy_start_locations[0]

            def _dist(t: int) -> float:
                u = bot.units.by_tag(int(t))
                return u.position.distance_to(enemy) if u is not None else 1e9

            return sorted(tags, key=_dist, reverse=(pos == "back"))
        except Exception as exc:
            logger.debug("_sort_by_position fail: %s", exc)
            return tags

    def _record_unit_semantics(self, directive: Directive, tags: list[int]) -> None:
        """指派单位时把它的识别语意（守的地点 / 任务 verb / 类型）挂到每个 tag。

        日后玩家"守瞭望塔的追猎去X" → 按这些语意标签匹配回 tag（不靠坐标，靠语意）。
        从 directive 的 payload 尽力抽 spot/verb/unit_type，抽不到不崩。
        """
        p = directive.payload
        verb, spot, utype = "", "", ""
        sel = getattr(p, "selector", None)
        if sel is not None:
            utype = getattr(sel, "unit_type", None) or ""
        action = getattr(getattr(p, "task", None), "primary_action", None)
        if action is not None:
            verb = str(getattr(action.verb, "value", action.verb) or "")
            tgt = getattr(action, "target", None)
            spot = (getattr(tgt, "named_spot", None) or "") if tgt is not None else ""
        else:  # move/scout 等无 task 的：用 payload.target + directive 类型当 verb
            tgt = getattr(p, "target", None)
            spot = (getattr(tgt, "named_spot", None) or "") if tgt is not None else ""
            verb = str(getattr(directive.type, "value", directive.type) or "")
        sem = {"verb": verb, "spot": spot, "unit_type": utype}
        for t in tags:
            self._unit_semantics[t] = dict(sem)

    def _resolve_by_semantics(self, sel: Any) -> list[int]:
        """按"指派时记下的语意"重选单位：assigned_spot(地点) / primary_verb_prefix(任务) + unit_type。"""
        spot = getattr(sel, "assigned_spot", None)
        prefix = getattr(sel, "primary_verb_prefix", None)
        utype = getattr(sel, "unit_type", None)
        out: list[int] = []
        for tag, sem in self._unit_semantics.items():
            if spot:
                rspot = sem.get("spot", "")
                # 模糊匹配:"watchtower" 命中 "watchtower_left/right";精确也命中。
                if not rspot or not (
                    rspot == spot or rspot.startswith(spot) or spot.startswith(rspot)
                ):
                    continue
            if prefix and not str(sem.get("verb", "")).startswith(prefix):
                continue
            if utype and str(sem.get("unit_type", "")).casefold() != utype.casefold():
                continue
            out.append(tag)
        # 2026-06-08 修 P1(探路农民去瞭望塔选错单位):"探路农民"的真身是 bot 的 ScoutWorker
        # 探机,bot 自动派的、没 vibecraft 语意记录 → primary_verb_prefix=scout 按 _unit_semantics
        # 选不到它(真局抓到:抓了**另一个**农民去瞭望塔,真探路农民没动)。补:prefix 命中 scout 时
        # 把 ScoutWorker 的实际 scout_tag 算进来(权威来源,放最前 → count=1 优先取它)。
        if prefix and str(prefix).lower().startswith("scout"):
            with contextlib.suppress(Exception):
                sw = getattr(self._bot, "scout_worker", None)
                st = getattr(sw, "scout_tag", None) if sw is not None else None
                if st is not None and int(st) not in out:
                    out.insert(0, int(st))
                    logger.info("CTRLTRACE scout_resolve used ScoutWorker scout_tag=%s", st)
        return self._filter_alive_tags(out)

    def _filter_alive_tags(self, tags: list[int]) -> list[int]:
        """剔除已死单位的 tag（防语意注册表里残留的死单位被选中）。"""
        if self._bot is None:
            return list(tags)
        try:
            alive = {u.tag for u in self._bot.units}
            return [t for t in tags if t in alive]
        except Exception:
            return list(tags)

    def _filter_by_unit_state(self, tags: list[int], sel: Any) -> list[int]:
        """WP-B: 按活体状态（血量/护盾）过滤 tags。

        health_below_pct / shield_below_pct 都为 None → 原样返回（不影响现有行为）。
        条件都设时取 AND（"血量低 AND 护盾低"）。
        读不到单位 / _bot 为 None / 取属性异常 → 该 tag 丢弃（过滤是"必须满足"语义）。
        """
        hp_thresh = getattr(sel, "health_below_pct", None)
        sh_thresh = getattr(sel, "shield_below_pct", None)
        if hp_thresh is None and sh_thresh is None:
            return tags
        if self._bot is None:
            # 无 bot 时无法确认血量 → 全部丢弃（不能放行）
            return []
        out: list[int] = []
        for tag in tags:
            try:
                unit = self._bot.units.by_tag(tag)
                if unit is None:
                    continue
                if hp_thresh is not None:
                    hp_pct = unit.health_percentage * 100
                    if hp_pct >= hp_thresh:
                        continue
                if sh_thresh is not None:
                    sh_pct = unit.shield_percentage * 100
                    if sh_pct >= sh_thresh:
                        continue
                out.append(tag)
            except Exception:
                # 取属性失败 → 无法确认，丢弃
                continue
        return out

    def _assign_standing_order_units(self, submitted: Directive) -> None:
        """P5.E: standing order submit 时解析 selector → tags + 通知 sharpy 让位
        + 立即下发首条 primary_action(2026-05-25 bug 1 修复)。

        2026-05-24 用户:selector.count 指定 → cap N 个;None 时不限(见
        _resolve_selector_with_count)。

        2026-05-25 bug 1:persistent unit_claim 路由进 standing_orders 不进
        _in_flight,_dispatch_committed_to_facade.pop 找不到 → 不调
        _apply_unit_claim → 没下 execute_unit_action → 单位被 reserved 但
        站原地。本函数末尾补 execute_unit_action 下发,跟 _apply_unit_claim
        路径(ephemeral 走的)对齐。
        """
        if not isinstance(submitted.payload, UnitClaimPayload):
            return
        payload = submitted.payload
        # vibecraft: 2026-06-06 persistent"回家防守/撤退"(standby→home)清过期全局 attack。
        # 放在 tag 解析前 —— 意图层面就该清,不依赖是否解析到单位(与 ephemeral 路径一致)。
        self._maybe_pullback_clear(
            payload.task.primary_action, float(getattr(self._bot, "time", 0.0))
        )
        # 2026-06-29 fix #580：recruit_new=True watcher 必须在 "if not tags: return" **之前**
        # 注册。group_harass / recruit_new claim 提交时往往还没有目标单位（BC 还没造好），
        # 若先判 tags 为空就 return，watcher 永远不注册 → 后来新出现的单位永远不被征入。
        # 修法：把 watcher 注册提前到 tag 解析前（seen 仍初始化为当前全量匹配结果）。
        if payload.recruit_new:
            ut = payload.selector.unit_type or ""
            seen_now_claim: set[int] = set(self.facade.resolve_selector(unit_type=ut) if ut else [])
            self._recruit_watchers[submitted.id] = {
                "kind": "claim",
                "group_id": None,
                "unit_type": ut,
                "seen": seen_now_claim,
            }
            logger.info(
                "RECRUIT register claim unit_type=%s seen=%d id=%s",
                ut,
                len(seen_now_claim),
                submitted.id[:8],
            )
        tags = self._resolve_selector_with_count(payload.selector)
        if not tags:
            return
        # WP-C:记录每个 tag 被本指令抢占前的归属,并从原主集合移除(独占所有权)
        _disp_so: dict[int, str | None] = {}
        for _t in tags:
            _prior_so = self._current_owner_of(_t, exclude_id=submitted.id)
            _disp_so[_t] = _prior_so
            if _prior_so is not None:
                self._standing_order_tags[_prior_so].discard(_t)
        self._displaced[submitted.id] = _disp_so
        self._standing_order_tags[submitted.id] = set(tags)
        # issue #3:抢到的单位若还被旧 MOVE 指令控制 → 取消那条 move(撕扯消失)
        self._supersede_conflicting_moves(
            set(tags), keep_id=submitted.id, now=float(getattr(self._bot, "time", 0.0))
        )
        self._record_unit_semantics(submitted, tags)  # 挂语意标签供日后重选
        action = payload.task.primary_action
        verb_str = action.verb.value
        # cast_ability 是 instant 命令,不该 persistent(LLM prompt 也不会这样组合);
        # defensive skip 避免重复 cast。group_harass / harass_workers：director 只维护 tag 集，
        # 微操由 director 每 tick 主动调度（_execute_worker_harass_micro / GroupHarassAct），不发一次性命令。
        skip_action = verb_str in ("cast_ability", "group_harass", "harass_workers")
        target_dump = action.target.model_dump(mode="json") if action.target else None
        # 2026-05-25 bug 11 诊断:log assign tags + target,让下次实测能区分
        # "set_role 失败" / "execute_unit_action 失败" / "sharpy 抢回 probe"。
        logger.info(
            "standing_order assign id=%s verb=%s tags=%s target=%s",
            submitted.id[:8],
            verb_str,
            tags,
            target_dump,
        )
        # 2026-06-01 Task F:patrol + waypoints → 注册 _pending_patrol（取首个 tag）。
        # 若 waypoints 缺失或解析失败，降级走普通 execute_unit_action（不崩）。
        if verb_str == "patrol":
            waypoints = getattr(action.target, "waypoints", None) if action.target else None
            if waypoints and len(waypoints) >= 2:
                pA = self._resolve_target_spec_point(waypoints[0])
                pB = self._resolve_target_spec_point(waypoints[1])
                if pA is not None and pB is not None:
                    for tag in tags:
                        self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
                        self._pending_patrol[submitted.id] = {
                            "tag": tag,
                            "points": [pA, pB],
                            "idx": 0,
                        }
                        # 立即发一次 move 到 points[0]
                        self.facade.execute_unit_action(
                            unit_tag=tag,
                            verb="move_to",
                            target={"kind": "point", "point": list(pA)},
                        )
                        break  # 每个 directive_id 只跟踪一个 tag（取第一个）
                    return
                logger.warning(
                    "patrol waypoints 解析失败，降级走普通 execute_unit_action id=%s",
                    submitted.id[:8],
                )
                # 降级：不拦截，继续走下方普通路径
            else:
                if waypoints is not None:
                    logger.warning(
                        "patrol waypoints 不足两点，降级走普通 execute_unit_action id=%s",
                        submitted.id[:8],
                    )
                # waypoints 为 None（旧格式/named_spot 单点 patrol）→ 降级走普通路径

        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            if not skip_action:
                self.facade.execute_unit_action(
                    unit_tag=tag,
                    verb=verb_str,
                    target=target_dump,
                    ability_id=action.ability_id,
                )

        # 2026-06-29 注意：recruit_new watcher 已在函数顶部（tags 解析之前）注册，
        # 确保 claim 提交时即使还没有匹配单位也能持续监听（fix #580 根因）。

    def _claim_directive_units(self, submitted: Directive) -> None:
        """2026-05-24 用户:被指令的单位暂时不接受 BOT 指令 → 所有有 selector 的
        directive 提交时 set_unit_role(LLM_CONTROLLED)。directive done/revoke 时
        通过 _release_standing_order_units 反向归还。

        覆盖:MOVE / SCOUT / UNIT_CLAIM(ephemeral & persistent) / UNIT_RELEASE。
        BUILD_AT 不直接选单位(后端 builder 自动);DROP_ACT 内部 reserve 棱镜。

        UNIT_RELEASE 语义反:不 reserve,实际归还(release_unit_role)+ 立即 done。
        """
        from vibecraft.directives.models import (
            MovePayload,
            ScoutPayload,
            UnitClaimPayload,
            UnitReleasePayload,
        )

        payload = submitted.payload
        # UnitClaim persistent 走 _assign_standing_order_units(已实现)
        if isinstance(payload, UnitClaimPayload) and payload.persistent:
            return
        # 取 selector
        selector = None
        if isinstance(payload, (UnitClaimPayload, MovePayload, UnitReleasePayload)):
            selector = payload.selector
        elif isinstance(payload, ScoutPayload):
            selector = payload.selector  # 可能 None(bot 自选 probe)
        if selector is None:
            return
        # 2026-06-04 走 group-aware 统一 helper(认 group_id/chain_id/语意/count)，
        # 否则"N 队进攻/移动"这类带 selector.group_id 的命令在 submit 预留路径
        # 解析为空(裸 resolve_selector 只认 unit_type/tag/tags) → 单位不被预留。
        try:
            tags = self._resolve_selector_with_count(selector)
        except Exception as exc:
            logger.debug("claim_directive_units resolve_selector fail: %s", exc)
            return
        if not tags:
            return
        # UNIT_RELEASE: 直接 release,不 reserve
        # 2026-05-25 bug audit:cap by selector.count(否则"释放一个农民"全释放,
        # 跟 _apply_unit_release 路径对齐 — bug 4 同 pattern submit 路径漏修)。
        if isinstance(payload, UnitReleasePayload):
            capped_tags = (
                tags[: selector.count]
                if (selector.count is not None and selector.count > 0)
                else tags
            )
            for tag in capped_tags:
                if hasattr(self.facade, "release_unit_role"):
                    self.facade.release_unit_role(tag)
            return
        # 2026-05-24 selector.count 指定 → cap(LLM 解"一个农民"→count=1,防全锁)
        if selector.count is not None and selector.count > 0:
            tags = tags[: selector.count]
        # MOVE/SCOUT/UNIT_CLAIM(ephemeral): reserve
        # WP-C:记录每个 tag 被本指令抢占前的归属,并从原主集合移除(独占所有权)
        _disp_ep: dict[int, str | None] = {}
        for _t in tags:
            _prior_ep = self._current_owner_of(_t, exclude_id=submitted.id)
            _disp_ep[_t] = _prior_ep
            if _prior_ep is not None:
                self._standing_order_tags[_prior_ep].discard(_t)
        self._displaced[submitted.id] = _disp_ep
        self._standing_order_tags[submitted.id] = set(tags)
        # issue #3:抢到的单位若还被旧 MOVE 指令控制 → 取消那条 move(撕扯消失)
        self._supersede_conflicting_moves(
            set(tags), keep_id=submitted.id, now=float(getattr(self._bot, "time", 0.0))
        )
        self._record_unit_semantics(submitted, tags)  # 挂语意标签供日后重选
        for tag in tags:
            with contextlib.suppress(Exception):
                self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)

    def _current_owner_of(self, tag: int, exclude_id: str) -> str | None:
        """WP-C: tag 当前被哪条 directive 控制(≠ exclude_id);无 → None。"""
        for did, tags in self._standing_order_tags.items():
            if did != exclude_id and tag in tags:
                return did
        return None

    def _supersede_conflicting_moves(self, tags: set[int], keep_id: str, now: float) -> None:
        """issue #3:新指令抢到 tags 时,把这些 tag 从其它 MOVE 指令(_safe_move_tags)
        移除;旧 move 丢光单位 → 标已终止消失。

        WP-C(_current_owner_of+_displaced)只覆盖 standing↔standing 的所有权转移;
        MOVE 走 _safe_move_tags(不在 _standing_order_tags)→ 之前漏。这里补上:
        玩家"虚空贴边对方主矿"(move)后又"一队回家防守"(claim),两条都控同一批虚空
        每帧下冲突 move → 部队抽搐。新指令抢走单位即取消旧 move,撕扯消失。

        _pending_move 是 selector 未解析、无具体 tag,不在此处理(它有 90s timeout 兜底)。
        """
        if not tags:
            return
        tagset = set(tags)
        for mid, entry in list(self._safe_move_tags.items()):
            if mid == keep_id:
                continue
            mtags = entry[0]
            if not (mtags & tagset):
                continue
            remaining = mtags - tagset
            engage = entry[2] if len(entry) > 2 else False
            if remaining:
                self._safe_move_tags[mid] = (remaining, entry[1], engage)
            else:
                self._safe_move_tags.pop(mid, None)
                d = self._in_flight.get(mid)
                if d is not None:
                    logger.info(
                        "MOVE %s 被新指令 %s 抢走全部单位 → superseded", mid[:8], keep_id[:8]
                    )
                    self._release_directive_done(d, now, reason="superseded")

    def _reissue_primary_action(self, tag: int, directive_id: str) -> bool:
        """WP-C: 重发某 directive 对 tag 的首要动作(恢复时用)。成功 True。"""
        d = self._in_flight.get(directive_id) or self._committed_directives.get(directive_id)
        if d is None:
            for so in getattr(self, "standing_orders", []):
                if getattr(so, "id", None) == directive_id:
                    d = so
                    break
        if d is None:
            return False
        p = getattr(d, "payload", None)
        task = getattr(p, "task", None)
        try:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            if task is not None and getattr(task, "primary_action", None) is not None:
                action = task.primary_action
                self.facade.execute_unit_action(
                    unit_tag=tag,
                    verb=action.verb.value,
                    target=action.target.model_dump(mode="json") if action.target else None,
                    ability_id=getattr(action, "ability_id", None),
                )
            else:
                # move/scout:用 payload.target + verb=move_to/scout
                tgt = getattr(p, "target", None)
                dt = str(getattr(getattr(d, "type", None), "value", "") or "")
                verb = "scout" if dt == "scout" else "move_to"
                if tgt is not None:
                    self.facade.execute_unit_action(
                        unit_tag=tag,
                        verb=verb,
                        target=tgt.model_dump(mode="json"),
                    )
            return True
        except Exception as exc:
            logger.debug("reissue_primary_action fail tag=%s did=%s: %s", tag, directive_id, exc)
            return False

    def _restore_unit_to_prior(self, tag: int, prior_id: str) -> bool:
        """WP-C: 把 tag 恢复给 prior 指令(case 1)。

        prior 已结束/单位已死/失败 → False(调用方交回 bot)。
        单位已死 → True(静默跳过,不交 bot)。
        """
        # prior 仍活 = 它的 key 还在 _standing_order_tags(它自己 release 时会 pop 掉)
        if prior_id not in self._standing_order_tags:
            return False  # case 3:prior 已结束
        bot = getattr(self, "_bot", None)
        if bot is not None:  # case 4:单位已死 → 不恢复也不交 bot,静默跳过(返 True 表示已处理)
            try:
                if bot.units.by_tag(tag) is None:
                    return True
            except Exception:
                return True
        self._standing_order_tags[prior_id].add(tag)
        return self._reissue_primary_action(tag, prior_id)

    def _release_standing_order_units(self, directive_id: str) -> None:
        """P5.E + WP-C: revoke_standing_order 时归还 sharpy 让位 + 按位恢复被抢占单位。

        WP-C 恢复逻辑:
        - case 1: prior 仍活 → 把 tag 加回 prior 集合 + 重发 prior primary_action
        - case 4: 单位已死 → 静默跳过
        - case 2/3: prior=None 或 prior 已结束 → 交回 bot(release_unit_role)
        """
        tags = self._standing_order_tags.pop(directive_id, set())
        displaced = self._displaced.pop(directive_id, {})
        for tag in tags:
            self._unit_semantics.pop(tag, None)  # 释放即清语意标签
            prior = displaced.get(tag)
            if prior is not None and self._restore_unit_to_prior(tag, prior):
                continue  # case 1/4:恢复给 prior(或单位死了静默跳过),不交 bot
            # case 2(prior=None) / case 3(prior 已结束):交回 bot
            if hasattr(self.facade, "release_unit_role"):
                self.facade.release_unit_role(tag)

    # ------------------------------------------------------------------
    # 语音编队（GROUP_ASSIGN / GROUP_CLEAR）
    # ------------------------------------------------------------------

    def _apply_group_assign(self, submitted: Directive) -> None:
        """GROUP_ASSIGN：把 selector 解析出的 tags 存入 _voice_groups[group_id]，SET 语义（替换）。"""
        payload = submitted.payload
        assert isinstance(payload, GroupAssignPayload)
        tags = self._resolve_selector_with_count(payload.selector)
        self._voice_groups[payload.group_id] = set(tags)
        logger.info(
            "group_assign: 队%d ← %d 单位 %s",
            payload.group_id,
            len(tags),
            tags,
        )

    def _apply_group_clear(self, submitted: Directive) -> None:
        """GROUP_CLEAR：解散编队，pop tags + release 每个 tag 的 unit role。"""
        payload = submitted.payload
        assert isinstance(payload, GroupClearPayload)
        tags = self._voice_groups.pop(payload.group_id, set())
        for tag in tags:
            if hasattr(self.facade, "release_unit_role"):
                self.facade.release_unit_role(tag)
        logger.info(
            "group_clear: 队%d 解散(%d 单位放回)",
            payload.group_id,
            len(tags),
        )
        # 规则3:解散编队连带撤销"针对该编队"的指令(如"一队进攻"unit_claim(group_id:1))。
        # 否则那条指令残留、每帧 re-Reserve 单位 → 全军撤退够不到(玩家报"取消编队+全军撤退
        # 虚空没退")。
        self._cancel_directives_controlling_units(
            tags, float(getattr(self._bot, "time", 0.0)), exclude_id=submitted.id
        )
        # 2026-06-13 持续征兵:解散队 N 时，把所有 kind="group" 且 group_id=N 的 watcher
        # 连带删除并 revoke 对应 directive（停止继续征兵）。
        now_clear = float(getattr(self._bot, "time", 0.0))
        for did, w in list(self._recruit_watchers.items()):
            if w.get("kind") == "group" and w.get("group_id") == payload.group_id:
                self._recruit_watchers.pop(did, None)
                with contextlib.suppress(Exception):
                    self.revoke_directive(did, now_clear)

    def _cancel_directives_controlling_units(
        self, tags: Any, now: float, exclude_id: str | None = None
    ) -> None:
        """规则3(2026-06-08 用户):释放/解散一批单位 → 连带撤销所有控制这些单位的 directive
        (撤退/进攻/待命卡等),彻底还给 bot。否则那些 directive 残留、每帧 re-Reserve 单位
        (玩家报"取消编队/释放虚空后,单位身上的旧指令还在、全军命令够不到")。
        """
        if not tags:
            return
        tagset = {int(t) for t in tags}
        to_cancel = [
            did
            for did, dtags in list(self._standing_order_tags.items())
            if did != exclude_id and (dtags & tagset)
        ]
        if to_cancel:
            logger.info(
                "CTRLTRACE cancel_controlling units=%d revoked=%d dids=%s",
                len(tagset),
                len(to_cancel),
                [d[:8] for d in to_cancel],
            )
        for did in to_cancel:
            with contextlib.suppress(Exception):
                self.revoke_directive(did, now)

    # ------------------------------------------------------------------
    # 剧本时机偏差检测(自动从 yaml phase + steps 推断)
    # ------------------------------------------------------------------

    # 神族 tech 建筑全集(只看科技建筑,不含 Pylon/Nexus/Assimilator/兵营 BG)
    _PROTOSS_TECH_STRUCTURES: frozenset[str] = frozenset(
        {
            "ROBOTICSFACILITY",
            "ROBOTICSBAY",
            "STARGATE",
            "FLEETBEACON",
            "TWILIGHTCOUNCIL",
            "TEMPLARARCHIVES",
            "DARKSHRINE",
            "FORGE",
        }
    )

    # 2026-05-24 用户:剧本时机检测改"补齐成本"(原固定 supply 阈值不准):
    # 缺的建筑总成本 > _OBSOLETE_COST_THRESHOLD → 时机已过。
    # cost = mineral + gas*1.5 + build_time*0.5(对齐 transition_cost.py 公式)。
    # 阈值 800 ≈ 缺 2 个 mid-tier 科技建筑(VC 325 + VR 382 ≈ 707)。
    _OBSOLETE_COST_THRESHOLD: float = 800.0

    # 2026-05-24 用户:完成的卡片延迟此秒数才真删,前端这段时间显示"已完成"
    # 用户反馈 5s 太长,改 2s。
    _DONE_GRACE_S: float = 2.0

    # 代理建造重发节流(游戏秒):农民空闲时隔此秒才重发 build,给它时间走到位开建,
    # 避免每帧重发 + find_placement 抖动导致目标乱跳、永远建不出来(2026-06-06 真局自验)。
    _PROXY_REISSUE_THROTTLE_S: float = 0.3

    # 2026-07-08 WORKER_TASK transfer_to_base:选中农民持续钉去新基地采矿的秒数
    # (对抗 sharpy DistributeWorkers 每帧按 ideal_harvesters 拉回)。到期释放归还 bot。
    _WORKER_TRANSFER_SETTLE_S: float = 8.0

    # WP-E bot 自评限频与触发常量
    _SELF_EVAL_COOLDOWN_S: float = 25.0  # 两条自评最短间隔
    _SELF_EVAL_ARMY_DROP: int = 6  # 军队人口掉这么多算"一波交战"
    _SELF_EVAL_TTL_S: float = 8.0  # 旁白在 snapshot 里的有效窗口

    def _check_strategy_obsolete(self, directive: Directive) -> list[str]:
        """检测剧本时机偏差。返回偏差原因列表(空 → 没过期)。

        判定:
        1. 建筑互斥:已造科技建筑中,有"该剧本 build steps 不需要的"
           (4bg 只需 CyberneticsCore → 若已有 RoboticsFacility,偏差)
        2. 补齐成本:目标 build 当前 supply 应该已有的建筑,缺的总 cost
           (mineral + gas*1.5 + build_time*0.5)> _OBSOLETE_COST_THRESHOLD
           → 时机已过(2026-05-24 用户:替代原固定 supply 阈值)

        只对 OpeningBuild 检测(midgame / lategame 没有"必须前置建筑"语义)。
        """
        from vibecraft.directives.models import StrategySetPayload
        from vibecraft.strategy.aliases import VerbHint
        from vibecraft.strategy.models import OpeningBuild
        from vibecraft.strategy.unit_data import canonical_name, get_struct_cost

        payload = directive.payload
        if not isinstance(payload, StrategySetPayload):
            return []
        if self.library is None:
            return []
        try:
            strat = self.library.get(payload.strategy_id)
        except Exception:
            return []
        if not isinstance(strat, OpeningBuild):
            return []  # midgame/lategame 不检测

        reasons: list[str] = []
        try:
            state = self.facade.get_state()
        except Exception:
            return []

        # ---- 1. 建筑互斥 ----
        allowed = {step.obj.upper() for step in strat.parsed_steps() if step.verb == "build"}
        forbidden = self._PROTOSS_TECH_STRUCTURES - allowed
        actual_forbidden = state.structures_built & forbidden
        if actual_forbidden:
            names = "/".join(sorted(actual_forbidden))
            reasons.append(_i18n_t("obsolete.builtConflict", self._lang, names=names))

        # ---- 2. 补齐成本 ----
        # 把当前 supply 应该已有的 build steps 转成 expected_structures dict
        # (canonical PascalCase 名 → 期望数量),与当前 structures_built 对比。
        current_structs = {canonical_name(n) for n in state.structures_built}
        expected: dict[str, int] = {}
        for step in strat.parsed_steps():
            if step.verb != "build" or step.supply > state.supply_used:
                continue
            try:
                canonical, group = self.library.aliases.resolve(step.obj, verb=VerbHint.BUILD)
            except Exception:
                continue
            if group != "building":
                continue
            expected[canonical] = expected.get(canonical, 0) + 1

        missing_cost = 0.0
        missing_names: list[str] = []
        for canonical, target_count in expected.items():
            have = 1 if canonical in current_structs else 0
            missing = max(0, target_count - have)
            if missing > 0:
                cost_data = get_struct_cost(canonical)
                # 复用 transition_cost.py 公式(mineral + gas*1.5 + build_time*0.5)
                cost = cost_data.mineral + cost_data.gas * 1.5 + cost_data.build_time * 0.5
                missing_cost += missing * cost
                missing_names.append(canonical)

        if missing_cost > self._OBSOLETE_COST_THRESHOLD:
            names = ",".join(missing_names)
            reasons.append(
                _i18n_t(
                    "obsolete.fillCost",
                    self._lang,
                    cost=int(missing_cost),
                    threshold=int(self._OBSOLETE_COST_THRESHOLD),
                    names=names,
                )
            )

        return reasons

    def confirm_force_strategy(self, now: float) -> None:
        """玩家在 PWA 点 [硬转] → 强制 submit 之前被拦的 STRATEGY_SET。"""
        if self._pending_force_strategy is None:
            return
        directive, _reasons = self._pending_force_strategy
        self._pending_force_strategy = None
        directive = directive.model_copy(
            update={
                "issued_at": now,
                "source_text": (directive.source_text or "voice") + " (force)",
            }
        )
        submitted = self.board.submit(directive, now=now)
        self._in_flight[submitted.id] = submitted
        self._push_snapshot(now)

    def cancel_force_strategy(self) -> None:
        """玩家在 PWA 点 [取消] → drop 被拦的 directive。"""
        self._pending_force_strategy = None

    def revoke_standing_order(self, directive_id: str, now: float) -> bool:
        """玩家通过 revoke_directive 上行帧撤销 standing order（P1.2）。

        从 standing_orders 列表移除，释放 sharpy 让位（P5.E），
        通知 board（P5 已支持 committed overlay 撤销），并推一次 snapshot。
        向后兼容保留；P1.4+ 的新代码改用 revoke_directive。
        """
        before = len(self.standing_orders)
        self.standing_orders = [s for s in self.standing_orders if s.id != directive_id]
        if len(self.standing_orders) < before:
            # P5.E: 归还 sharpy 让位（LLM_CONTROLLED → sharpy 重新接管）
            self._release_standing_order_units(directive_id)
            # 通知 board（P5: board.revoke 现已支持 committed overlays）
            self.board.revoke(directive_id, now)
            self._push_snapshot(now)
            return True
        return False

    def revoke_production_override(self, directive_id: str, now: float) -> bool:
        """从 production_overrides 列表移除指定 directive（P2）。

        通知 board + 推 snapshot，语义镜像 revoke_standing_order。
        """
        before = len(self.production_overrides)
        self.production_overrides = [s for s in self.production_overrides if s.id != directive_id]
        if len(self.production_overrides) < before:
            self.board.revoke(directive_id, now)
            self._override_status.pop(directive_id, None)
            self._production_item_status.pop(directive_id, None)
            self._push_snapshot(now)
            return True
        return False

    # ------------------------------------------------------------------
    # WP-D 实时运营策略层（双维度）
    # ------------------------------------------------------------------

    def apply_macro_action(self, dim: str, value: object, now: float) -> None:
        """玩家点运营策略控件 → 应用对应 macro action（三维度，独立互不干扰）。

        dim == "expand"：
          value "one_more" → fire-and-forget：提交一张 expansion_override(current+1) 卡，
                             新矿建好后卡自动标 done 消失。**开矿维度只有这一个值**
                             （封顶 expand=N/max/clear 已于 2026-07-27 随前端入口一并下架；
                             ExpansionOverridePayload 本身保留——one_more 与 LLM 的
                             「最多开 N 个矿」都还走它）。
        dim == "workers"：
          value "stop"    → ProductionBlockPayload(unit_type="Probe")
          value "max"     → 满采模式（_tick_worker_saturation 每 tick 补农民）
          value "default" → 撤掉本维度 override，回 bot 默认
        dim == "mining"：
          value "mineral"  → 优先水晶（set_mining_priority("mineral")，不下指令卡）
          value "gas"      → 优先气（set_mining_priority("gas")，不下指令卡）
          value "default"  → 恢复剧本默认（set_mining_priority(None)，不下指令卡）
        """
        from vibecraft.directives.models import ExpansionOverridePayload, ProductionBlockPayload
        from vibecraft.directives.types import IssuedBy

        if dim == "expand":
            if value == "one_more":
                # fire-and-forget：提交一张 current+1 扩张卡，不碰旧卡槽、不封顶。
                # base count = ready 基地数 + 在建 Nexus/Hatch/CC 数。
                bot = getattr(self, "_bot", None)
                if bot is not None:
                    try:
                        from sc2.ids.unit_typeid import UnitTypeId as _UTI

                        _pending = int(bot.already_pending(_UTI.NEXUS))
                    except Exception:
                        try:
                            # 虫族 / 人族 fallback：pending 任意 townhall
                            _pending = 0
                        except Exception:
                            _pending = 0
                    current = len(bot.townhalls.ready) + _pending
                else:
                    current = 1  # fallback（无 bot 时保守值）
                target = current + 1
                payload = ExpansionOverridePayload(target_count=target)
                directive = Directive(
                    payload=payload,
                    issued_at=now,
                    issued_by=IssuedBy.VOICE,
                    source_text="macro_action: expand=one_more",
                )
                self._submit_directives([directive], now)
                logger.info(
                    "macro_action expand=one_more → current=%d target=%d dir=%s",
                    current,
                    target,
                    directive.id[:8],
                )
                self._push_snapshot(now)
                return

            # 开矿维度只剩「多开一个矿」(上面已 return)。封顶 expand=N/max/clear 已于
            # 2026-07-27 随前端入口一并下架 —— 面板上没有这个控件了。
            logger.warning("macro_action expand 只支持 one_more,收到 value=%r,忽略", value)
            return

        if dim == "workers":
            # 先撤旧 worker override
            if self._worker_block_dir_id is not None:
                did = self._worker_block_dir_id
                with contextlib.suppress(Exception):
                    self.revoke_production_override(did, now)
                with contextlib.suppress(Exception):
                    if did in self._in_flight:
                        self.revoke_directive(did, now)
                self._worker_block_dir_id = None
            self._worker_mode = None

            if value == "default":
                logger.info("macro_action workers cleared, back to bot default")
                self._push_snapshot(now)
                return

            if value == "stop":
                payload = ProductionBlockPayload(unit_type="Probe")
                directive = Directive(
                    payload=payload,
                    issued_at=now,
                    issued_by=IssuedBy.VOICE,
                    source_text="macro_action: workers=stop",
                )
                self._submit_directives([directive], now)
                self._worker_block_dir_id = directive.id
                self._worker_mode = "stop"
                logger.info("macro_action workers=stop dir=%s", directive.id[:8])
            elif value == "max":
                # 满采模式：不下 directive，靠 _tick_worker_saturation 每帧补
                self._worker_mode = "max"
                logger.info("macro_action workers=max (saturation tick enabled)")

        elif dim == "mining":
            # 采矿策略维度：持续状态，不发指令卡，走 facade.set_mining_priority
            if value == "mineral":
                self._mining_priority = "mineral"
                self.facade.set_mining_priority("mineral")
                logger.info("macro_action mining=mineral (水晶优先)")
            elif value == "gas":
                self._mining_priority = "gas"
                self.facade.set_mining_priority("gas")
                logger.info("macro_action mining=gas (气矿优先)")
            elif value == "default":
                self._mining_priority = None
                self.facade.set_mining_priority(None)
                logger.info("macro_action mining=default (恢复剧本默认)")
            else:
                logger.warning("macro_action mining unknown value=%s", value)
                return

        elif dim == "upgrade_target":
            # 攻防升级目标等级：{family: str, level: int|'auto'} payload
            if not isinstance(value, dict):
                logger.warning("macro_action upgrade_target: value 必须是 dict，got %r", value)
                return
            family = value.get("family")
            raw_level = value.get("level")
            if not isinstance(family, str) or family not in self._UPGRADE_CAP_FAMILIES:
                logger.warning(
                    "macro_action upgrade_target: family=%r 不在白名单或不是 str", family
                )
                return
            if raw_level == "auto":
                # 设为自动 → pop（None=auto）
                self._upgrade_targets.pop(family, None)
                self.facade.set_upgrade_target(family, None)
                logger.info(
                    "upgrade_target_set family=%s level=auto (restored to bot default)", family
                )
            else:
                try:
                    lvl = int(raw_level)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    logger.warning(
                        "macro_action upgrade_target: level=%r 不合法（需 0-3 或 'auto'）",
                        raw_level,
                    )
                    return
                if lvl not in (0, 1, 2, 3):
                    logger.warning("macro_action upgrade_target: level=%d 超范围（需 0-3）", lvl)
                    return
                self._upgrade_targets[family] = lvl
                self.facade.set_upgrade_target(family, lvl)
                logger.info("upgrade_target_set family=%s level=%d", family, lvl)

        else:
            logger.warning("macro_action unknown dim=%s value=%s", dim, value)
            return

        self._push_snapshot(now)

    def _tick_worker_saturation(self) -> None:
        """满采补农民 tick（每帧调用，仅 _worker_mode=="max" 时生效）。

        算法（WP4 账目分离）：
          cap = Σ ideal_harvesters(非 stealth 主基地 ready) + Σ ideal_harvesters(气矿 ready)
          cur = supply_workers - 存活 stealth 农民数（stealth_worker_tags）
          need = cap - cur
          if need > 0: bot.train(PROBE, amount=need)

        账目分离理由（WP4）：偷矿农民由 StealthCellManager 就地自产管理；若主矿同时
        把 stealth 农民算进 supply_workers 对比全部 ideal（含 stealth Nexus）→ 双重生产
        + 主矿欠饱和。账目分离让主矿只对"自己的 ideal"负责，stealth cell 只对自己的负责。

        ideal_harvesters 反映剩余矿量，采空了的 patch 自动减，不会过采。
        train() 资源/产能不足时返回 0 但不抛异常，下 tick 重试，安全。
        """
        if self._worker_mode != "max" or self._bot is None:
            return
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            # 账目分离：stealth townhall 不计入主矿 cap
            stealth_th_tags = self._stealth_manager.stealth_townhall_tags
            stealth_worker_count = len(self._stealth_manager.stealth_worker_tags)

            cap = sum(
                int(getattr(th, "ideal_harvesters", 0))
                for th in self._bot.townhalls.ready
                if th.tag not in stealth_th_tags
            ) + sum(int(getattr(g, "ideal_harvesters", 3)) for g in self._bot.gas_buildings.ready)
            # stealth 农民已纳入 supply_workers，但不属于主矿；减去以避免主矿少补
            cur = int(getattr(self._bot, "supply_workers", 0)) - stealth_worker_count
            need = cap - cur
            if need > 0:
                try:
                    self._bot.train(UnitTypeId.PROBE, amount=need, train_only_idle_buildings=False)
                except Exception as _train_exc:
                    logger.debug("worker_saturation train failed: %s", _train_exc)
        except Exception as exc:
            logger.debug("worker_saturation tick error: %s", exc)

    # ------------------------------------------------------------------
    # 2026-05-24 Clarification (LLM 给玩家选项时的玩家响应入口)
    # ------------------------------------------------------------------

    def submit_clarification_choice(self, option_index: int, now: float) -> bool:
        """玩家从 pending clarification 选了某 option → submit 该 option 的 directives。

        返回 True 表示成功 submit;False 表示无 pending clarification 或 index 越界。
        """
        cr = self._pending_clarification
        if cr is None:
            return False
        if option_index < 0 or option_index >= len(cr.options):
            logger.warning("clarification choice OOB: idx=%d len=%d", option_index, len(cr.options))
            return False
        chosen = cr.options[option_index]
        logger.info(
            "clarification chosen idx=%d label=%r → submitting %d directives",
            option_index,
            chosen.label,
            len(chosen.directives),
        )
        self._pending_clarification = None
        # source_text 沿用 clarification 原话(玩家选的还是源自这条原话)
        for d in chosen.directives:
            if not d.source_text:
                d_copy = d.model_copy(update={"source_text": cr.source_text})
                chosen_directives = [d_copy if x is d else x for x in chosen.directives]
                chosen.directives[:] = chosen_directives
                break
        self._submit_directives(list(chosen.directives), now)
        with contextlib.suppress(Exception):
            self._push_snapshot(now)
        return True

    def cancel_clarification(self, now: float) -> bool:
        """玩家点 ❌ → 清掉 pending clarification,不 submit 任何 directive。"""
        if self._pending_clarification is None:
            return False
        logger.info("clarification cancelled")
        self._pending_clarification = None
        with contextlib.suppress(Exception):
            self._push_snapshot(now)
        return True

    def revoke_directive(self, directive_id: str, now: float) -> bool:
        """统一撤销接口（P2/P0g Task 11）：L3 standing → L4 production → L2 tactical → L1 strategy。

        ws.py 和 bot._tick_view_channel 的 revoke_directive 分支改调此方法，
        不再直接调用 revoke_standing_order。

        2026-05-24 用户:撤销时统一释放 _standing_order_tags 内的 reserved units
        (覆盖 _claim_directive_units 设的 ephemeral 单位)。
        """
        # #4:取消前先抓 directive(算 display 给历史终态记录),removal 后就找不到了
        _revoked_d = self._find_directive(directive_id)
        # 统一释放该 directive reserved 的 units(若有)
        self._release_standing_order_units(directive_id)
        # 顺手清 _in_flight(若是单位级 ephemeral directive 在 _in_flight)
        self._in_flight.pop(directive_id, None)
        # 2026-05-25 bug 5:清掉 commit 后保留的 ephemeral directive
        self._committed_directives.pop(directive_id, None)
        # safe_move 清 tags(不再 tick 控位)
        self._safe_move_tags.pop(directive_id, None)
        # 2026-05-27 Issue 3:玩家 × 清 pending move(unit 还没造出来就取消)
        self._pending_move.pop(directive_id, None)
        # 2026-06-01 Task F:清巡逻状态
        self._pending_patrol.pop(directive_id, None)
        # 2026-06-13 持续征兵:× 时停止继续征兵（auto_enroll / recruit_new）
        # 只停止 watcher，不解散编队（编队保留，取消持续指令语义）
        self._recruit_watchers.pop(directive_id, None)
        # 2026-06-06 代理建造:× 时停掉 tick(农民 tag 已由上面 _release_standing_order_units 放归)
        self._pending_proxy_build.pop(directive_id, None)
        # 2026-06-07 玩家折跃:× 时取消还没折完的折跃请求
        warp_keys = self._warp_registered.pop(directive_id, None)
        if warp_keys:
            for k in warp_keys:
                with contextlib.suppress(Exception):
                    self.facade.cancel_warp(k)
        # 2026-06-06 问题5c:灰色"未激活"卡(挂在 _pending_activation)也要能 × 掉,否则永远撤不掉
        self._pending_activation.pop(directive_id, None)
        result = self._revoke_directive_dispatch(directive_id, now)
        # #4:手动取消成功 → 记终态 cancelled(历史展开显示"已手动取消")
        if result:
            self._record_terminal(directive_id, "cancelled", _revoked_d)
        return result

    def _revoke_directive_dispatch(self, directive_id: str, now: float) -> bool:
        """revoke_directive 的分层派发（L3→L4→view→block→phoenix→L2→L1）。"""
        if self.revoke_standing_order(directive_id, now):
            return True
        if self.revoke_production_override(directive_id, now):
            return True
        # 2026-05-30 view_follow revoke：清 active_view_follow_id + 停止 follow
        if directive_id == self._active_view_follow_id:
            self._active_view_follow_id = None
            self._push_snapshot(now)
            return True
        # 2026-06-07 出兵集结点 revoke：清 _rally_point → 停每帧续设,恢复 bot 默认前移。
        # pop 掉 committed 卡(_record_terminal "cancelled" 由 revoke_directive 调用方记)。
        if directive_id == self._rally_point_id:
            self._rally_point = None
            self._rally_point_id = None
            with contextlib.suppress(Exception):
                self.facade.set_rally_point(None)
            self._committed_directives.pop(directive_id, None)
            self._push_snapshot(now)
            return True
        # 2026-05-30 production_block revoke：unblock unit_type
        if directive_id in self._production_blocks:
            return self._apply_production_block_revoke(directive_id, now)
        # 2026-05-30 凤凰骚扰卡 revoke：玩家点× → 凤凰停止骚扰归队主力
        if directive_id == "phoenix_harass" and self._phoenix_harass is not None:
            self._end_phoenix_harass(now, reason="player_cancel")
            return True
        if self.revoke_tactical(directive_id, now):
            return True
        return self.revoke_strategy(directive_id, now)

    def revoke_tactical(self, directive_id: str, now: float) -> bool:
        """L2 撤销：清 override flag (A 类) 或释放 squad unit (B 类)。

        A 类（attack/defend/retreat 等）：清 facade override flag，重置 _current_l2_global_id。
        B 类（harass/scout）：遍历 squad.unit_tags，调 facade.release_unit_role 还给 sharpy，
        然后从 _tactical_squads 移除。
        两类可共存（同一 directive 极罕见，但防御处理）。
        """
        cleared = False

        # A 类：override flag 路径
        if directive_id in self._tactical_overrides:
            self._tactical_overrides.pop(directive_id, None)
            if self._current_l2_global_id == directive_id:
                try:
                    self.facade.set_attack_target_override(None)
                    self.facade.set_combat_intent_override(None)
                    # 2026-05-25 用户:清 attack_mode_override("强制/试探")
                    set_mode = getattr(self.facade, "set_attack_mode_override", None)
                    if set_mode is not None:
                        set_mode(None)
                    # 2026-05-28 用户反馈:玩家 × defend/retreat(persistent)的卡片
                    # 后 stance_override 没清,bot 永远 stance=defend/retreat →
                    # _should_attack 看 stance in ("hold","defend","retreat")→ False
                    # → bot 永远不主动 attack。补清 stance_override。
                    set_stance = getattr(self.facade, "set_engagement_stance", None)
                    if set_stance is not None:
                        set_stance(None)
                    # 2026-05-28: hold 用 hold_gather_point,× 时也要清(防 PlanZoneGather
                    # 持续读到老聚团点)
                    set_hgp = getattr(self.facade, "set_hold_gather_point", None)
                    if set_hgp is not None:
                        set_hgp(None)
                except Exception as exc:  # pragma: no cover
                    logger.debug("revoke_tactical facade clear fail: %s", exc)
                self._current_l2_global_id = None
            cleared = True

        # B 类：squad 路径
        if directive_id in self._tactical_squads:
            squad = self._tactical_squads.pop(directive_id)
            for tag in squad.unit_tags:
                try:
                    self.facade.release_unit_role(tag)
                except Exception as exc:  # pragma: no cover
                    logger.debug("revoke_tactical release_unit_role(%s) fail: %s", tag, exc)
            cleared = True

        if cleared:
            self._override_status.pop(directive_id, None)
            # board.revoke 若找不到此 id 也不报错（tactical 可能未经 board.submit）
            try:
                self.board.revoke(directive_id, now)
            except Exception as exc:  # pragma: no cover
                logger.debug("revoke_tactical board.revoke fail: %s", exc)
            if self._current_l2_global_id is None:
                self._current_l2_global_directive = None
            self._push_event(
                {
                    "type": "event",
                    "kind": "directive.revoked",
                    "ts": now,
                    "payload": {"directive_id": directive_id, "reason": "player_x"},
                }
            )
            self._push_snapshot(now)

        return cleared

    def revoke_strategy(self, directive_id: str, now: float) -> bool:
        """L1 撤销：清 board.slots[stage] + 自动切 persistent doctrine（两层架构）。

        接受两种 directive_id 形式：
        - "l1_{stage.value}" 占位 id（Task 10 约定），如 "l1_midgame"
        - 无前缀时尝试按 slot 匹配（当前 StrategySlot 无 directive_id 字段，不支持）
        """
        target_stage: StageKind | None = None

        if directive_id.startswith("l1_"):
            suffix = directive_id[3:]
            try:
                target_stage = StageKind(suffix)
            except Exception:
                return False
        else:
            # StrategySlot 当前没有 directive_id 字段，无法按真实 id 匹配
            return False

        if self.board.slots.get(target_stage) is None:
            return False

        slot = self.board.slots[target_stage]
        strategy_id = slot.strategy_id if slot is not None else None
        self.board.slots[target_stage] = None

        # 两层架构：cancel 触发自动 persistent 切换（不再降级 sustain）
        self._apply_auto_persistent_switch(
            now, reason="cancel_redirected", caused_by=f"revoke:{directive_id}"
        )

        self.session.log_event(
            Event(
                ts=now,
                kind=EventKind.DIRECTIVE_RELEASED,
                payload={
                    "directive_id": directive_id,
                    "stage": target_stage.value,
                    "strategy_id": strategy_id,
                    "reason": "player_revoke",
                },
                priority="medium",
                caused_by="player_x",
            )
        )
        self.session.log(
            LogStream.DIRECTIVES,
            {
                "ts": round(now, 3),
                "event": "released",
                "directive_id": directive_id,
                "reason": "player_revoke",
            },
        )
        self._push_event(
            {
                "type": "event",
                "kind": "directive.revoked",
                "ts": now,
                "payload": {"directive_id": directive_id, "reason": "player_x"},
            }
        )
        self._push_snapshot(now)
        return True

    def _dispatch_cancel(self, directive: Directive, now: float) -> None:
        """兼容入口（已废弃旁路）。转发到 _apply_strategy_cancel。

        原来直接旁路调用，现在走 board.submit → _apply_to_facade → _apply_strategy_cancel。
        此方法保留防止外部仍有调用；内部不再从 _submit_directives 调用。
        """
        if not isinstance(directive.payload, StrategyCancelPayload):
            return
        self._apply_strategy_cancel(directive.payload, now, directive_id=directive.id)

    def _apply_strategy_cancel(
        self, payload: StrategyCancelPayload, now: float, directive_id: str
    ) -> None:
        """STRATEGY_CANCEL commit 后执行：清 board slot + 自动切 persistent doctrine
        （两层架构 2026-05-19）+ log + push snapshot。

        由 _apply_to_facade 调用（board.submit → commit → 这里），不再是旁路直调。
        Cancel 不再降级 sustain；改为 pick_best_persistent + facade.set_build(chosen)。
        """
        cleared_stages: list[StageKind] = []
        targets: list[StageKind] = (
            list(StageKind) if payload.stage == "all" else [StageKind(payload.stage)]
        )
        for stage in targets:
            if self.board.slots.get(stage) is not None:
                self.board.slots[stage] = None
                cleared_stages.append(stage)
        # commit 后把 STRATEGY_CANCEL directive 从 board.overlays 移出（它已执行，不需持续活跃）
        self.board.overlays = [d for d in self.board.overlays if d.id != directive_id]

        # 两层架构：自动切 persistent doctrine（取代旧的 set_build("sustain")）
        self._apply_auto_persistent_switch(
            now, reason="cancel_redirected", caused_by=f"voice:cancel:{directive_id}"
        )

        # 清掉推荐
        self._pending_recommendation = None
        # log cancel 事件
        self.session.log_event(
            Event(
                ts=now,
                kind=EventKind.STRATEGY_SET,
                payload={
                    "action": "cancel",
                    "cleared_stages": [s.value for s in cleared_stages],
                    "directive_id": directive_id,
                },
                priority="medium",
                caused_by="voice",
            )
        )
        self._push_snapshot(now)

    # ------------------------------------------------------------------
    # 两层架构（2026-05-19）：自动选 persistent doctrine 切换
    # ------------------------------------------------------------------

    def _build_game_snapshot_for_cost(self) -> Any:
        """从 facade 构造 transition_cost.GameSnapshot。

        facade 的 state 是全大写 SDK name（structures_built / army_summary /
        upgrades），必须 canonical_name 转成 PascalCase 才能跟 doctrine yaml 的
        required_* / target_composition 对上 —— 否则 transition_cost 算出来
        每个 doctrine 都是"全缺"，成本塌成绝对值、不区分开局状态。
        """
        from vibecraft.strategy.transition_cost import GameSnapshot
        from vibecraft.strategy.unit_data import canonical_name

        try:
            state = self.facade.get_state()
        except Exception:
            state = None

        if state is None:
            return GameSnapshot()

        # structures_built 是 set（只有有无、没计数）→ canonical 名 + 计 1
        structures = {canonical_name(n): 1 for n in state.structures_built}
        units = {canonical_name(k): v for k, v in state.army_summary.items()}
        upgrades = {canonical_name(u) for u in state.upgrades}
        # gas_income 没有直接 telemetry，用 gas 值 / 30 估算（粗略）
        gas_income_est = float(state.gas) / 30.0 if state.gas > 0 else 50.0

        return GameSnapshot(
            structures=structures,
            units=units,
            upgrades=upgrades,
            researching=set(),
            gas_income_per_minute=gas_income_est,
        )

    def _compute_enemy_tags(self) -> set[str]:
        """从 facade 拉敌情，推断 enemy composition tag 集合。"""
        from vibecraft.strategy.enemy_tags import compute_enemy_composition_tags
        from vibecraft.strategy.unit_data import canonical_name

        try:
            state = self.facade.get_state()
        except Exception:
            return set()

        # enemy_summary 是全大写 SDK name → canonical 转 PascalCase
        # （compute_enemy_composition_tags 内部按 "Roach"/"Marine" 等 PascalCase 查）
        return compute_enemy_composition_tags(
            enemy_summary={canonical_name(k): v for k, v in state.enemy_summary.items()},
            enemy_race=state.enemy_race,
            enemy_upgrades=set(),
        )

    def _active_build_has_core_units(self) -> bool:
        """当前 active build 是否声明了 core_units（build-aware sustain）。

        声明了 → sustain delay=0（opening 一完成立即接管产能扩张，2026-06-15）。
        """
        try:
            recipe = getattr(self._bot, "active_recipe", "") or ""
            if self.library is None or not recipe:
                return False
            build = self.library.get(recipe)
            return bool(getattr(build, "core_units", None))
        except Exception:
            return False

    def notify_opening_completed(self, now: float, caused_by: str = "bot:opening_done") -> bool:
        """bot 调用:当前开局策略达到完成条件 → **推荐**切持续策略(发 toast 但不换 plan)。

        典型触发点:4bg 的 `_ready_to_pressure` 条件全满足(折跃完成 + 4 BG ready +
        4 stalker)时,gate4_pressure 的 EmitOpeningCompleteAct 调用本方法。

        关键设计(2026-05-20 用户反馈修正):
        与 cancel 不同 — opening_completed **只推荐不真换 plan**(swap_plan=False)。
        原因:`_ready_to_pressure` 触发于 4 stalker 刚 warp 完即将出门那一刻,
        此时换 plan 会把 gate4 的 ForwardWarpStalker + PlanZoneAttack 整个
        撤掉,后续不再 warp + 4 stalker 没人指挥停在原地。Toast 让玩家看到推荐,
        自己挑时机切(攻击打完之后)。

        本方法一次性 — 之后无论 bot 调多少次都不再触发(`_opening_completed_signaled`
        latch),避免重复推 toast。

        Args:
            now: 当前 game_time(秒)
            caused_by: 事件链路追踪,默认 "bot:opening_done"

        Returns:
            True = 本次触发了 toast;False = 之前已触发过 / library 没 persistent
            doctrine,等价无操作。
        """
        if self._opening_completed_signaled:
            return False
        chosen = self._apply_auto_persistent_switch(
            now, reason="opening_completed", caused_by=caused_by, swap_plan=False
        )
        if chosen is None:
            # pick_best_persistent 失败(race 没注册 persistent / library 空),
            # 不 latch,允许将来重试。
            return False
        self._opening_completed_signaled = True
        # 2026-05-27 Task #341: 记录 opening 完成时刻,供 sustain uncap 超时检查。
        self._opening_completed_at = now
        return True

    def notify_phoenix_harass_started(self, started_at: float, deadline: float) -> bool:
        """bot 调用：PhoenixSquadAct 攒够凤凰 launch 骚扰时触发，创建凤凰骚扰持久指令卡。

        2026-05-30 用户设计：凤凰攒 6 个出骚扰 → 创建一张持久指令卡（玩家可见）。
        - 玩家点× 卡片 → 凤凰停止骚扰归队大部队（revoke_directive 特判）。
        - now >= deadline（plan 据剧本给的硬性截止时间）→ 自动收卡 + 凤凰归队。

        本方法一次性（已有卡时重复调忽略）。卡片 id 固定 "phoenix_harass"。

        Args:
            started_at: 骚扰开始 game_time（秒）
            deadline:   硬性截止 game_time（秒），到点自动归队

        Returns:
            True = 本次创建了卡片；False = 已有 active 卡，忽略。
        """
        if self._phoenix_harass is not None:
            return False
        self._phoenix_harass = {"started_at": float(started_at), "deadline": float(deadline)}
        # 确保 flag active（默认即 True，显式置一次防之前局残留 / 重开）
        try:
            self.facade.set_phoenix_harass_active(True)
        except Exception as exc:  # pragma: no cover
            logger.debug("notify_phoenix_harass_started set active fail: %s", exc)
        logger.warning(
            "phoenix_harass card created (start=%.1f deadline=%.1f)", started_at, deadline
        )
        self._push_snapshot(started_at)
        return True

    def _end_phoenix_harass(self, now: float, reason: str) -> None:
        """收凤凰骚扰卡：set flag False（凤凰归队主力）+ 清 state + 推 snapshot。"""
        if self._phoenix_harass is None:
            return
        self._phoenix_harass = None
        try:
            self.facade.set_phoenix_harass_active(False)
        except Exception as exc:  # pragma: no cover
            logger.debug("_end_phoenix_harass set inactive fail: %s", exc)
        logger.warning("phoenix_harass card ended (reason=%s, t=%.1f)", reason, now)
        self._push_snapshot(now)

    def _apply_auto_persistent_switch(
        self,
        now: float,
        reason: str,
        caused_by: str | None = None,
        swap_plan: bool = True,
    ) -> str | None:
        """调 pick_best_persistent 算成本，切到最低成本 doctrine + 推送 PWA。

        Args:
            now: 当前 game_time（秒）
            reason: "cancel_redirected" / "opening_completed" / "parse_fail_redirected"
            caused_by: 事件链路追踪
            swap_plan: 是否真换 bot plan(调 facade.set_build)。
                - True(默认):cancel 等场景,立即换 plan
                - False:opening_completed 等场景,只发 toast 推荐,plan 不动
                  (gate4 的 attack 逻辑要继续跑;否则 4 stalker 一 warp 完 plan 就被
                  替换 → 没人指挥 + 不再 warp,见 2026-05-20 用户反馈)

        Returns:
            chosen doctrine id；如 my_race 没 persistent doctrine 注册或 library 为 None
            则返回 None（无操作）。
        """
        import contextlib

        # library 优先用 self.library；fallback 到 parser.library（parser 必有）
        library = self.library if self.library is not None else self.parser.library
        if library is None:
            logger.warning("auto_persistent_switch: no library, skip")
            return None
        my_race = (self.parser.my_race or "").lower()
        if not my_race:
            logger.warning("auto_persistent_switch: no my_race, skip")
            return None

        from vibecraft.strategy.transition_cost import pick_best_persistent

        snapshot = self._build_game_snapshot_for_cost()
        enemy_tags = self._compute_enemy_tags()

        try:
            chosen, cost, all_costs = pick_best_persistent(snapshot, enemy_tags, library, my_race)
        except ValueError as exc:
            # race 没注册 persistent doctrine（如未跑过 Step 5 迁移 / 仅 1g_robo 测试库）
            logger.warning("auto_persistent_switch: pick_best failed (%s)", exc)
            return None

        # facade.set_build（即时生效）
        if swap_plan:
            with contextlib.suppress(Exception):
                self.facade.set_build(chosen)

        # log auto_switch 事件（PWA 据此显示 toast）
        sorted_costs = sorted(all_costs.items(), key=lambda kv: kv[1])
        alternatives = [{"id": sid, "cost": round(c, 1)} for sid, c in sorted_costs[1:4]]
        evt = Event(
            ts=now,
            kind=EventKind.STRATEGY_AUTO_SWITCH,
            payload={
                "reason": reason,
                "chosen_id": chosen,
                "cost": round(cost, 1),
                "alternatives": alternatives,
                "enemy_tags_hit": sorted(enemy_tags),
                # swap_plan=True → bot 已切;False → 仅推荐,玩家自己确认才切
                "swap_plan": swap_plan,
            },
            priority="medium",
            caused_by=caused_by,
        )
        self.session.log_event(evt)
        # PWA push（toast 显示）
        self._push_event(evt.model_dump(mode="json"))
        logger.info(
            "auto_persistent_switch[%s]: chose %s (cost=%.1f, race=%s, enemy_tags=%d, swap=%s)",
            reason,
            chosen,
            cost,
            my_race,
            len(enemy_tags),
            swap_plan,
        )
        return chosen

    # ------------------------------------------------------------------
    # M3 L4 sharpy 真出兵 wire (production_override → bot.train)
    # ------------------------------------------------------------------

    def _emit_production_queued_event(self, directive: Directive, now: float) -> None:
        """L4 directive 入 production_overrides 时 emit 一条 PWA event 告诉玩家已收到。

        语义:"将加入生产队列",1.5s commit 后实际开始 train。
        """
        from vibecraft.directives.models import (
            ExpansionOverridePayload,
            ProductionOverridePayload,
            TechOverridePayload,
        )

        p = directive.payload
        lang = self._lang
        if isinstance(p, ProductionOverridePayload):
            items_text = " / ".join(f"{it.unit_type} × {it.count}" for it in p.items)
            display = _i18n_t("queue.unitsAdded", lang, items=items_text)
        elif isinstance(p, TechOverridePayload):
            display = _i18n_t("queue.upgradeAdded", lang, upgrade=p.upgrade_id)
        elif isinstance(p, ExpansionOverridePayload):
            display = _i18n_t("queue.expandAdded", lang, count=p.target_count)
        else:
            display = _i18n_t("queue.genericAdded", lang, kind=directive.type.value)
        event_dict = {
            "type": "event",
            "kind": "directive.queued",
            "ts": round(now, 3),
            "payload": {"directive_id": directive.id, "display": display},
        }
        self._push_event(event_dict)

    async def execute_overrides_step(self, now: float) -> None:
        """每 sharpy bot step 调用,**async**(expand_now 是 async)。

        分发 L4 三类 override 到对应 handler:
        - production_override → bot.train(unit_id) 抢 building action slot
        - tech_override       → bot.research(upgrade_id)
        - expansion_override  → await bot.expand_now()

        增量语义:不重复 train/research(用 bot.already_pending 防 spam)。
        done 判定由 task_monitor (counter / tech_done flag / expansion_count
        checker) 自动 mark + board.complete pop overrides list。
        """
        from vibecraft.directives.models import (
            DropActPayload,
            ExpansionOverridePayload,
            ProductionOverridePayload,
            StructureOverridePayload,
            TechOverridePayload,
        )

        if self._bot is None:
            return

        if self.production_overrides:
            # 用 list copy 防迭代时 board.complete pop 改 list
            for d in list(self.production_overrides):
                # 2026-05-24 跳过 done grace 期内的 directive(不再 train/build)
                if d.id in self._done_at:
                    continue
                payload = d.payload
                if isinstance(payload, ProductionOverridePayload):
                    self._exec_production_override(d, payload)
                elif isinstance(payload, TechOverridePayload):
                    self._exec_tech_override(d, payload)
                elif isinstance(payload, ExpansionOverridePayload):
                    await self._exec_expansion_override(d, payload)
                elif isinstance(payload, StructureOverridePayload):
                    await self._exec_structure_override(d, payload)
                elif isinstance(payload, DropActPayload):
                    self._exec_drop_act(d, payload)

            # 每 tick 调 _active_drop_acts 内所有 ActBase.execute()
            # (directive-driven PrismWarpDropAct / GenericDropAct)
            for _act_id, _act in list(self._active_drop_acts.items()):
                try:
                    done = await _act.execute()
                    if done:
                        self._active_drop_acts.pop(_act_id, None)
                except Exception as _exc:
                    logger.debug("drop_act[%s].execute() error: %s", _act_id, _exc)

            # WP-D 维度2：满采补农民 tick
            try:
                self._tick_worker_saturation()
            except Exception as _ws_exc:
                logger.debug("worker_saturation tick uncaught: %s", _ws_exc)

        # 2026-07-08 人族建筑起飞/移动：独立于 production_overrides(玩家可能只下这
        # 一条指令),async 因为要 await can_place_single 找降落位。
        try:
            await self._tick_structure_move(now)
        except Exception as exc:
            logger.debug("_tick_structure_move fail: %s", exc)

    def _resolve_target_for_tech_tree(self, name: str) -> UnitTypeId | UpgradeId | None:
        """auto-detect:UPPER name → UnitTypeId 或 UpgradeId enum。

        优先 UpgradeId(BLINKTECH/STIMPACK 等),再 UnitTypeId(DARKTEMPLAR/ZERGLING)。
        两者 name 不冲突。返回 None 表示未知名字。
        """
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.ids.upgrade_id import UpgradeId

        try:
            return UpgradeId[name]
        except KeyError:
            pass
        try:
            return UnitTypeId[name]
        except KeyError:
            return None

    def _auto_build_prereqs_for(self, target_name: str, now: float) -> str:
        """缺失 prereq 时,自动补建整条 chain(2026-05-23 用户:扩到三族)。

        target_name 是 unit/structure/upgrade 的 UPPER name(如 "DARKTEMPLAR" /
        "BLINKTECH" / "ZERGLING")。auto-detect UpgradeId vs UnitTypeId,然后用
        tech_tree.prereq_chain() 拿到完整链,emit 一条 structure_override directive。

        三族通用:数据源 python-sc2 内置 PROTOSS/ZERG/TERRAN_TECH_REQUIREMENT +
        UPGRADE_RESEARCHED_FROM,Blizzard game data 自动维护。

        防重复:_auto_prereq_emitted 记录已 emit 过的 structure name。
        Returns: 用户可见描述,空 string 表示无事可做。
        """
        if self._bot is None:
            return ""
        target_id = self._resolve_target_for_tech_tree(target_name)
        if target_id is None:
            return ""

        from vibecraft.bot.tech_tree import equivalent_structures, prereq_chain

        try:
            chain = prereq_chain(target_id, self._bot.race)
        except Exception as exc:
            logger.debug("prereq_chain lookup failed for %s: %s", target_name, exc)
            return ""
        if not chain:
            return ""

        # 2026-05-25 bug 9 修复:玩家手动派的 structure_override 也算 "pending"。
        # 之前 auto_prereq 只看 game state(structures.ready + already_pending),
        # 玩家提交"修两个 BF" production_overrides 但 worker 还没真去 build → 这俩
        # 信号都是 0 → 误判 Forge 缺失 → 派冗余 1bf 卡片。
        pending_override_types: set[str] = set()
        for d_override in self.production_overrides:
            payload_override = d_override.payload
            items = getattr(payload_override, "items", None)
            if items:
                for it in items:
                    st_name = getattr(it, "structure_type", None) or getattr(it, "unit_type", None)
                    if st_name:
                        pending_override_types.add(str(st_name).upper())

        to_build: list[Any] = []  # list of UnitTypeId
        for struct_id in chain:
            struct_name = struct_id.name
            if struct_name in self._auto_prereq_emitted:
                continue
            # 玩家已派 structure_override 含此 structure_type → 跳过 auto_prereq
            if struct_name.upper() in pending_override_types:
                continue
            # 检查 morph 等价(Gateway 已建或 Warpgate 已建都算 ready)
            equiv = equivalent_structures(struct_id)
            ready = False
            pending = False
            for s_id in equiv:
                try:
                    if len(self._bot.structures(s_id).ready) > 0:
                        ready = True
                        break
                    if float(self._bot.already_pending(s_id)) > 0:
                        pending = True
                except Exception:
                    pass
            if ready or pending:
                continue
            to_build.append(struct_id)

        if not to_build:
            return ""

        # 2026-05-23 用户:每个缺失建筑/科技都创建一个任务,按依赖顺序逐个完成。
        # 拆成多个独立 structure_override directive(每个 1 item),PWA 上 N 张独立
        # 卡片 → 玩家清晰看到 chain 进度,而不是 1 张多 item 卡。
        #
        # 必须走 self._submit_directives(不是 board.submit)—— 后者只入 pending 队列,
        # 不 append production_overrides → PWA command_cards 看不到。修过这个 bug
        # 后,4 张卡片都会显示。每张卡 priority=80 抢资源优于普通生产。
        try:
            names: list[str] = []
            directives_to_submit: list[Directive] = []
            for s_id in to_build:
                payload = StructureOverridePayload(
                    # addon_decided=True: bot 内部决策,不触发玩家弹窗
                    items=[
                        StructureItem(structure_type=s_id.name, target_count=1, addon_decided=True)
                    ],
                    priority=80,
                )
                directive = Directive(
                    payload=payload,
                    issued_at=now,
                    priority=80,
                    source_text=f"auto_prereq:{target_name}",
                )
                directives_to_submit.append(directive)
                names.append(s_id.name)
            self._submit_directives(directives_to_submit, now)
            for n in names:
                self._auto_prereq_emitted.add(n)
            logger.info("auto_prereq emit chain for %s: %s", target_name, names)
            return _i18n_t("reason.autoBuild", self._lang, names=", ".join(names))
        except Exception as exc:
            logger.warning("auto_prereq emit failed for %s: %s", target_name, exc)
            return ""

    def _emit_addon_build(self, addon_name: str, now: float) -> str:
        """emit 一张 structure_override 给某挂件(addon)本体（不是它的 prereq 链）。

        2026-06-17 #542：坦克/掠夺者/女妖需要生产建筑挂 TechLab，但 tech_tree 的
        prereq 只到父楼（Factory），不含挂件。这里直接为挂件本体下一张 structure_override
        （走 addon 执行路径 builder.build），priority=80 抢资源。dedup 用 _auto_prereq_emitted。
        """
        if addon_name in self._auto_prereq_emitted:
            return ""
        try:
            payload = StructureOverridePayload(
                # addon_decided=True: bot 内部发出,不触发玩家弹窗
                items=[
                    StructureItem(structure_type=addon_name, target_count=1, addon_decided=True)
                ],
                priority=80,
            )
            directive = Directive(
                payload=payload,
                issued_at=now,
                priority=80,
                source_text=f"auto_addon:{addon_name}",
            )
            self._submit_directives([directive], now)
            self._auto_prereq_emitted.add(addon_name)
            logger.info("auto_addon emit %s", addon_name)
            return _i18n_t("reason.autoAddon", self._lang, addon=addon_name)
        except Exception as exc:
            logger.warning("auto_addon emit failed for %s: %s", addon_name, exc)
            return ""

    def _check_prereq_ready(self, item_canonical_name: str) -> tuple[bool, str]:
        """检查 unit/structure/upgrade 的 prereq structure 是否 ready(2026-05-23 重写)。

        数据源:tech_tree.required_for(走 python-sc2 内置三族 TECH_REQUIREMENT
        + UPGRADE_RESEARCHED_FROM)。比旧的手写 _REQUIRED_STRUCTURE 三族通用。

        item_canonical_name: UPPER enum name(如 "DARKTEMPLAR" / "BLINKTECH" /
        "DARKSHRINE")。auto-detect UpgradeId / UnitTypeId。
        返回 (ready, missing_name)。ready=True 时 missing_name=''。
        未知名字当无 prereq(silent skip,兼容旧行为)。
        """
        if self._bot is None:
            return (False, item_canonical_name)
        target_id = self._resolve_target_for_tech_tree(item_canonical_name)
        if target_id is None:
            return (True, "")  # 未知 → 假定无 prereq,跟旧行为兼容

        from vibecraft.bot.tech_tree import (
            _BASE_STRUCTURES,
            equivalent_structures,
            required_for,
        )

        try:
            req = required_for(target_id, self._bot.race)
        except Exception:
            return (True, "")
        if req is None:
            return (True, "")
        # 基础建筑(Pylon/SupplyDepot/Nexus/Hatchery 等)假定 ready,
        # 玩家自然有,sharpy supply manager 自动补 Pylon/SupplyDepot。
        if req in _BASE_STRUCTURES:
            return (True, "")

        # morph 等价检查(Gateway ↔ Warpgate)
        equiv = equivalent_structures(req)
        try:
            for s_id in equiv:
                if len(self._bot.structures(s_id).ready) > 0:
                    return (True, "")
            for s_id in equiv:
                if float(self._bot.already_pending(s_id)) > 0:
                    return (False, f"{req.name}({_i18n_t('tech.tBuilding', self._lang)})")
            return (False, req.name)
        except Exception:
            return (False, req.name)

    # ------------------------------------------------------------------
    # Task #523: 指令跨族校验
    # ------------------------------------------------------------------

    # 目标族工人 canonical 名（心灵控制/拥有判定用）
    _WORKER_OF: ClassVar[dict[str, str]] = {
        "protoss": "Probe",
        "terran": "SCV",
        "zerg": "Drone",
    }
    # 目标族主基地 canonical 名（拥有判定用）
    _BASE_OF: ClassVar[dict[str, str]] = {
        "protoss": "Nexus",
        "terran": "CommandCenter",
        "zerg": "Hatchery",
    }

    def _reject_if_cross_race(self, d: Directive) -> bool:
        """指令涉及单位/建筑种族 ≠ 玩家种族时友好拒绝。

        返回 True = 已拒绝（调用方 continue 跳过路由）；False = 放行。

        规则（2026-06-13 Task #523）：
        - 未知 canonical（race_of=None）或玩家种族未知（my_race=""）→ 放行（宁漏不误拦）。
        - UNIT_CLAIM / GROUP_ASSIGN：facade 真实拥有该单位 → 放行（否则拒绝）。
        - 其余类型：facade 拥有目标族农民（如心灵控制 Probe）或目标族主基地 → 放行。
        - facade 缺方法或抛异常 → 放行（安全兜底）。
        """
        from vibecraft.strategy.aliases import race_of

        my_race = (getattr(self.parser, "my_race", None) or "").lower()
        if not my_race:
            return False

        payload = d.payload
        canonicals: list[str] = []
        is_selector_check = False  # True = UNIT_CLAIM/GROUP_ASSIGN "真拥有该单位"例外路径

        if isinstance(payload, ProductionOverridePayload):
            canonicals = [item.unit_type for item in payload.items]
        elif isinstance(payload, StructureOverridePayload):
            canonicals = [item.structure_type for item in payload.items]
        elif isinstance(payload, BuildAtPayload):
            canonicals = [payload.structure_type]
        elif isinstance(payload, ProductionBlockPayload):
            canonicals = [payload.unit_type]
        elif isinstance(payload, (UnitClaimPayload, GroupAssignPayload)):
            if payload.selector.unit_type:
                canonicals = [payload.selector.unit_type]
            is_selector_check = True
        elif isinstance(payload, DropActPayload):
            canonicals = [payload.cargo_unit, payload.transport]
        else:
            return False  # 其余类型不校验

        for canonical in canonicals:
            target_race = race_of(canonical)
            if target_race is None or target_race == my_race:
                continue  # 未知名词 / 同族 → 放行

            # 跨族——检查例外（是否真实拥有目标族单位/农民）
            try:
                if is_selector_check:
                    # UNIT_CLAIM/GROUP_ASSIGN：真实拥有该单位则放行
                    if hasattr(self.facade, "resolve_selector") and self.facade.resolve_selector(
                        unit_type=canonical
                    ):
                        continue
                else:
                    # 拥有目标族农民或主基地 → 放行（心灵控制 / 多元族场景）
                    worker = self._WORKER_OF.get(target_race)
                    base = self._BASE_OF.get(target_race)
                    has_worker = bool(
                        worker
                        and hasattr(self.facade, "resolve_selector")
                        and self.facade.resolve_selector(unit_type=worker)
                    )
                    has_base = bool(
                        base
                        and hasattr(self.facade, "resolve_selector")
                        and self.facade.resolve_selector(unit_type=base)
                    )
                    if has_worker or has_base:
                        continue
            except Exception:
                continue  # facade 异常 → 安全放行

            # 确认拒绝
            unit_zh = self._loc.unit(canonical)
            target_race_zh = self._loc.race(target_race)
            my_race_zh = self._loc.race(my_race)
            reason = _i18n_t(
                "validate.crossRace",
                self._lang,
                unit=unit_zh,
                target_race=target_race_zh,
                my_race=my_race_zh,
            )
            logger.warning(
                "cross_race_rejected: my_race=%s target_race=%s canonical=%s directive_id=%s",
                my_race,
                target_race,
                canonical,
                d.id[:8],
            )
            self._in_flight[d.id] = d
            self._set_override_status(d, "failed", reason)
            return True

        return False

    def _reject_if_invalid_structure_type(self, d: Directive) -> bool:
        """build 类指令的 structure_type 必须是建筑，不能是单位（如 Battlecruiser）。

        返回 True = 已拒绝（调用方 continue 跳过路由）；False = 放行。

        规则（2026-06-19 Task #558）：
        - BuildAtPayload.structure_type 或 StructureOverridePayload.items[*].structure_type
          必须在别名表里属于 "building" group。
        - 属于 "unit" group（如 Battlecruiser / Marine）→ 拒绝，提示"不是建筑，你是想维修吗？"。
        - 未知 canonical（group=None）→ 放行（宁漏不误拦）。
        """
        from vibecraft.strategy.aliases import group_of

        payload = d.payload
        structure_types: list[str] = []

        if isinstance(payload, BuildAtPayload):
            structure_types = [payload.structure_type]
        elif isinstance(payload, StructureOverridePayload):
            structure_types = [item.structure_type for item in payload.items]
        else:
            return False  # 其他类型不校验

        for st in structure_types:
            grp = group_of(st)
            if grp is None:
                continue  # 未知 canonical → 放行
            if grp != "building":
                reason = (
                    _i18n_t("validate.notBuildingRepair", self._lang, st=st)
                    if grp == "unit"
                    else _i18n_t("validate.notBuilding", self._lang, st=st)
                )
                logger.warning(
                    "invalid_structure_type_rejected: structure_type=%s group=%s directive_id=%s",
                    st,
                    grp,
                    d.id[:8],
                )
                self._in_flight[d.id] = d
                self._set_override_status(d, "failed", reason)
                return True

        return False

    def _set_override_status(self, d: Directive, status: str, reason: str = "") -> None:
        """更新 directive 的 status。**只 status 切换时**才 emit event(防 spam):
        active 阶段 reason 可能高频变化("研究中 2% / 4% / 6%"),不让每次都 emit。
        snapshot 字段照样透传最新 reason(PWA 可看到 progress % 但不被 event 刷屏)。
        """
        cur = self._override_status.get(d.id, {})
        prev_status = cur.get("status")
        self._override_status[d.id] = {"status": status, "reason": reason}
        if prev_status == status:
            return  # status 没变,reason 变化不 emit event
        # status 真切换 → emit event 让 PWA 卡片 update color
        self._push_event(
            {
                "type": "event",
                "kind": "directive.status_changed",
                "ts": 0,  # PWA 自己用接收时间
                "payload": {
                    "directive_id": d.id,
                    "status": status,
                    "reason": reason,
                },
            }
        )

    def _forward_warp_reference_point(self) -> tuple[float, float] | None:
        """ "刷到前线"折跃的参考点 = 敌方主基地 → 选离敌最近的能量场(最靠前的野水晶/棱镜)。

        2026-06-09 用户:前线折跃要落在最靠前的能量场,不是我方前沿基地。返回敌方起始点
        (始终已知),交给 facade._nearest_power_source 选离它最近的 PYLON/已展开 WARPPRISM。
        拿不到敌方点返回 None(调用方回退到原 _forward 解析)。
        """
        bot = self._bot
        if bot is None:
            return None
        try:
            esl = getattr(bot, "enemy_start_locations", None)
            if esl:
                p = esl[0]
                return (float(p.x), float(p.y))
        except Exception as exc:
            logger.debug("forward warp ref point fail: %s", exc)
        return None

    def _exec_production_override(self, d: Directive, payload: Any) -> None:
        """L4 unit 出兵: 遍历 items 逐个 bot.train(unit_id)。带 prereq check + per-item status。

        多兵种语义：整条 directive 完成 = 所有 item 都下满。每个 item 状态独立
        存到 _production_item_status[did][unit_type]，让 PWA 多兵种合并卡片
        每条进度行能单独显示"缺前置 / 资源不足 / 生产中 / 完成"。

        ARCHON 特殊路径(2026-05-24):ARCHON 不能 train,要 2 DT/HT merge。
        走 _exec_archon_item 智能合球(优先 DT 路径)。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _UTID

        item_status: dict[str, dict[str, str]] = {}
        on_hold_reasons: list[str] = []
        any_active = False
        all_satisfied = True
        # 2026-06-07 "在X刷N兵":warp_at 落点 → 折跃门兵种折跃在最近能量场(不走家里 train)。
        warp_point: tuple[float, float] | None = None
        if getattr(payload, "warp_at", None) is not None:
            warp_point = self._resolve_target_spec_point(payload.warp_at)
            # 2026-06-09 用户:"刷到前线"的前线能量场 = 最靠前的野水晶 / 已展开前压棱镜,
            # **不是**我方前沿基地。named_spot="forward" 经 _forward 解析成我方靠前 NEXUS
            # (在野水晶后方很远)→ _nearest_power_source 会选离它最近的"家里"水晶(踩坑:
            # 真局折跃到了家里 (129,108) 而非前线野水晶 (89,29))。改用**敌方主基地**作参考点
            # → facade._nearest_power_source 选"离敌最近的能量场"= 最靠前的野水晶 OR 已展开棱镜
            # (该函数本就同时考虑 PYLON 和 WARPPRISMPHASING,棱镜自动纳入)。
            if getattr(payload.warp_at, "named_spot", None) == "forward":
                fwd = self._forward_warp_reference_point()
                if fwd is not None:
                    warp_point = fwd
        for item in payload.items:
            unit_id = self._resolve_unit_type_id(item.unit_type)
            if unit_id is None:
                logger.warning("resolve unit_type_id fail: %r", item.unit_type)
                item_status[item.unit_type] = {
                    "state": "blocked",
                    "reason": _i18n_t("reason.unknownUnit", self._lang),
                }
                on_hold_reasons.append(
                    f"{item.unit_type}: {_i18n_t('reason.unknownUnit', self._lang)}"
                )
                all_satisfied = False
                continue
            # 折跃门兵种 + 指定了落点 → 走折跃(facade.request_warp 幂等 + warp_status 查进度),
            # 不 train(否则家里又出一份)。能量场暂无 → warp_status="producing"(挂着等)。
            if warp_point is not None and self._is_warp_capable(item.unit_type):
                key = f"{d.id}:{item.unit_type}"
                self._warp_registered.setdefault(d.id, set()).add(key)
                self.facade.request_warp(key, item.unit_type, item.count, warp_point)
                if self.facade.warp_status(key) == "done":
                    item_status[item.unit_type] = {"state": "done", "reason": ""}
                else:
                    item_status[item.unit_type] = {
                        "state": "producing",
                        "reason": _i18n_t("reason.warping", self._lang),
                    }
                    any_active = True
                    all_satisfied = False
                continue
            # ARCHON 不能 train,走 merge 路径
            if unit_id == _UTID.ARCHON:
                now_ts = float(getattr(self._bot, "time", 0.0))
                status = self._exec_archon_item(d, item, now_ts)
                item_status[item.unit_type] = status
                if status["state"] == "done":
                    pass
                elif status["state"] == "producing":
                    any_active = True
                    all_satisfied = False
                else:
                    on_hold_reasons.append(f"{item.unit_type}: {status['reason']}")
                    all_satisfied = False
                continue
            # 归一名(VIKING→VIKINGFIGHTER)用于 prereq 检查 + 自动补建,
            # 否则占位 enum VIKING 无 prereq → 缺 Starport 时不会自动补建机场。
            canonical_name = self._UNIT_NAME_MAP.get(item.unit_type.upper(), item.unit_type.upper())
            ready, missing = self._check_prereq_ready(canonical_name)
            if not ready:
                # 2026-05-23 用户:自动补齐依赖(扩到三族)。直接传 unit_type,
                # _auto_build_prereqs_for 走 tech_tree.prereq_chain() 拿完整链,
                # 不再依赖外面给的 missing。已 emit 过的 structure 不重复。
                auto_msg = ""
                try:
                    now_ts = float(getattr(self._bot, "time", 0.0))
                    auto_msg = self._auto_build_prereqs_for(canonical_name, now_ts)
                except Exception as exc:
                    logger.debug("auto_prereq invocation failed: %s", exc)
                reason = _i18n_t("reason.need", self._lang, missing=missing)
                if auto_msg:
                    reason = _i18n_t("reason.needAuto", self._lang, missing=missing, auto=auto_msg)
                item_status[item.unit_type] = {"state": "blocked", "reason": reason}
                on_hold_reasons.append(f"{item.unit_type}: {reason}")
                all_satisfied = False
                continue
            # 挂件前置(2026-06-17 #542)：坦克/掠夺者/女妖等 requires_techlab 的兵种，
            # 普通 prereq(tech_tree)只到父楼、不含挂件 → 父楼没挂 TechLab 时 train 出不来。
            # 检查：需要 TechLab 且没有任一生产楼挂了 → 自动补挂件，本兵种这帧挂起等挂件。
            if self._unit_requires_techlab(unit_id) and not self._producer_has_techlab(unit_id):
                addon_name = self._techlab_addon_name_for(unit_id)
                auto_msg = ""
                if addon_name:
                    try:
                        now_ts = float(getattr(self._bot, "time", 0.0))
                        auto_msg = self._emit_addon_build(addon_name, now_ts)
                    except Exception as exc:
                        logger.debug("auto techlab addon fail: %s", exc)
                reason = _i18n_t("reason.needTechlab", self._lang)
                if auto_msg:
                    reason = _i18n_t("reason.needTechlabAuto", self._lang, auto=auto_msg)
                item_status[item.unit_type] = {"state": "blocked", "reason": reason}
                on_hold_reasons.append(f"{item.unit_type}: {reason}")
                all_satisfied = False
                continue
            try:
                in_flight = float(self._bot.already_pending(unit_id))
            except Exception:
                in_flight = 0.0
            already_done = self._production_override_built_count(d, item.unit_type)
            remaining = item.count - already_done - int(in_flight)
            if remaining <= 0:
                # 该 item 已下满（in_flight 含队列；已造完或全队列里了）
                if already_done >= item.count:
                    item_status[item.unit_type] = {"state": "done", "reason": ""}
                else:
                    item_status[item.unit_type] = {
                        "state": "producing",
                        "reason": _i18n_t("reason.queued", self._lang, n=int(in_flight)),
                    }
                continue
            all_satisfied = False
            try:
                n_trained = self._bot.train(
                    unit_id, amount=remaining, train_only_idle_buildings=False
                )
                if n_trained > 0:
                    logger.info(
                        "production_override TRAIN %s ×%d (count=%d, done=%d, in_flight=%.0f, id=%s)",
                        unit_id,
                        n_trained,
                        item.count,
                        already_done,
                        in_flight,
                        d.id[:8],
                    )
                    item_status[item.unit_type] = {"state": "producing", "reason": ""}
                    any_active = True
                else:
                    item_status[item.unit_type] = {
                        "state": "waiting",
                        "reason": _i18n_t("reason.insufficient", self._lang),
                    }
                    on_hold_reasons.append(
                        f"{item.unit_type}: {_i18n_t('reason.insufficient', self._lang)}"
                    )
            except Exception as exc:
                logger.debug("production_override train fail: %s", exc)
                item_status[item.unit_type] = {
                    "state": "waiting",
                    "reason": _i18n_t("reason.trainFail", self._lang),
                }

        # 持久化 per-item 状态供 snapshot 用
        self._production_item_status[d.id] = item_status

        if all_satisfied:
            self._set_override_status(d, "active", _i18n_t("production.ordered", self._lang))
        elif any_active:
            self._set_override_status(d, "active", "; ".join(on_hold_reasons))
        else:
            self._set_override_status(d, "on_hold", "; ".join(on_hold_reasons))

    def _exec_archon_item(self, d: Directive, item: Any, now_ts: float) -> dict[str, str]:
        """ARCHON merge 路径(2026-05-24 用户:"合白球"卡住)。

        ARCHON 不能 train,要 2 DT/HT merge。每 tick 评估:
        1. 现有 archon 数 >= 目标 → done
        2. DT pair 可用(>= 2 + 未在 merge 中) → 发 MORPH_ARCHON(同 ability
           的两个 UnitCommand,python-sc2 自动 combine 成 group action)
        3. HT pair 可用 → 同上(HT 优先级 < DT)
        4. cargo 不够 2:优先 train DT(若 DARKSHRINE 没好,_auto_build_prereqs_for
           自动补);DT 路径完全不可用(DARKSHRINE 缺 + 不能建) → train HT
        """
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.unit_command import UnitCommand

        bot = self._bot
        already_done = self._production_override_built_count(d, "Archon")
        try:
            cur_archons = int(bot.units(UnitTypeId.ARCHON).ready.amount)
        except Exception:
            cur_archons = 0
        total_archons = already_done + cur_archons
        if total_archons >= item.count:
            return {"state": "done", "reason": ""}

        # Step 1: 已有 DT/HT pair → 立即 merge
        for cargo_type in (UnitTypeId.DARKTEMPLAR, UnitTypeId.HIGHTEMPLAR):
            try:
                cargos = bot.units(cargo_type).ready
            except Exception:
                continue
            cargos = [u for u in cargos if u.tag not in self._archon_merging_tags]
            if len(cargos) >= 2:
                a, b = cargos[0], cargos[1]
                self._archon_merging_tags.add(a.tag)
                self._archon_merging_tags.add(b.tag)
                try:
                    bot.do(UnitCommand(AbilityId.MORPH_ARCHON, a))
                    bot.do(UnitCommand(AbilityId.MORPH_ARCHON, b))
                    logger.info(
                        "archon_merge: 2x%s tags=(%d,%d) directive=%s",
                        cargo_type.name,
                        a.tag,
                        b.tag,
                        d.id[:8],
                    )
                except Exception as exc:
                    logger.warning("archon_merge MORPH_ARCHON fail: %s", exc)
                return {
                    "state": "producing",
                    "reason": _i18n_t("reason.merge2", self._lang, cargo=cargo_type.name),
                }

        # Step 2: cargo 不够 2,优先 train DT(便宜 + 用户偏好)
        for cargo_type in (UnitTypeId.DARKTEMPLAR, UnitTypeId.HIGHTEMPLAR):
            ready, _missing = self._check_prereq_ready(cargo_type.name)
            if not ready:
                # 自动补依赖(DARKSHRINE / TEMPLARARCHIVES)
                try:
                    self._auto_build_prereqs_for(cargo_type.name, now_ts)
                except Exception as exc:
                    logger.debug("archon auto_prereq fail: %s", exc)
                # 这个 cargo 路径不通,试下个(HT)
                continue
            try:
                n = bot.train(cargo_type, amount=1, train_only_idle_buildings=False)
                if n > 0:
                    logger.info(
                        "archon_merge: train 1x %s (path=%s, directive=%s)",
                        cargo_type.name,
                        cargo_type.name,
                        d.id[:8],
                    )
                    return {
                        "state": "producing",
                        "reason": _i18n_t("reason.makeArchon", self._lang, cargo=cargo_type.name),
                    }
            except Exception as exc:
                logger.debug("archon train %s fail: %s", cargo_type.name, exc)
        # 两条路径都不能 train(资源不够 + prereq 正在建)
        return {"state": "waiting", "reason": _i18n_t("reason.waitArchon", self._lang)}

    def _exec_tech_override(self, d: Directive, payload: Any) -> None:
        """L4 科技: bot.research(upgrade_id)。带 prereq check + status tracking。"""
        upgrade_id = self._resolve_upgrade_id(payload.upgrade_id)
        if upgrade_id is None:
            logger.warning("resolve upgrade_id fail: %r", payload.upgrade_id)
            return
        # prereq check —— upgrade name(UPPER enum)走 tech_tree.required_for
        ready, missing = self._check_prereq_ready(upgrade_id.name)
        if not ready:
            # 2026-05-23 用户:自动补齐依赖(扩到三族 + upgrade)。
            # 直接传 upgrade_id.name,tech_tree 走 UPGRADE_RESEARCHED_FROM 拿
            # researching building,再递归 prereq chain。
            auto_msg = ""
            try:
                now_ts = float(getattr(self._bot, "time", 0.0))
                auto_msg = self._auto_build_prereqs_for(upgrade_id.name, now_ts)
            except Exception as exc:
                logger.debug("auto_prereq invocation failed (tech): %s", exc)
            reason = _i18n_t("reason.need", self._lang, missing=missing)
            if auto_msg:
                reason = _i18n_t("reason.needAuto", self._lang, missing=missing, auto=auto_msg)
            self._set_override_status(d, "on_hold", reason)
            return
        # already_pending_upgrade(u) 返回研究进度 [0, 1]
        try:
            progress = float(self._bot.already_pending_upgrade(upgrade_id))
        except Exception:
            progress = 0.0
        if progress > 0.0:
            # 在研究中(或已完成) = active
            self._set_override_status(
                d, "active", _i18n_t("tech.researching", self._lang, pct=f"{progress * 100:.0f}")
            )
            return
        try:
            success = self._bot.research(upgrade_id)
            if success:
                logger.info("tech_override RESEARCH %s (id=%s)", upgrade_id, d.id[:8])
                self._set_override_status(d, "active", "")
            else:
                # 资源不够 / 没 idle research building
                self._set_override_status(d, "on_hold", _i18n_t("reason.insufficient", self._lang))
        except Exception as exc:
            # sharpy do() override 不让 BotAI.research 调(传 bool 报错);
            # 只第一次 log,sharpy plan 自带的 research 路径会接管(如果 plan 包含该 upgrade)
            dbg = f"_dbg_research_exc_{d.id}"
            if not getattr(self, dbg, False):
                setattr(self, dbg, True)
                logger.warning(
                    "tech_override BotAI.research(%s) 不可用(sharpy 限制),由 sharpy plan 自带 research 路径接管: %s",
                    upgrade_id,
                    exc,
                )
            # 走 fallback:如果 sharpy plan 已经在研究(progress > 0),仍 set active
            try:
                progress = float(self._bot.already_pending_upgrade(upgrade_id))
                if progress > 0.0:
                    self._set_override_status(
                        d,
                        "active",
                        _i18n_t("tech.researching", self._lang, pct=f"{progress * 100:.0f}"),
                    )
                    return
            except Exception:
                pass
            self._set_override_status(d, "on_hold", _i18n_t("tech.waitSharpy", self._lang))

    async def _exec_expansion_override(self, d: Directive, payload: Any) -> None:
        """L4 开矿: await bot.expand_now()。带 status tracking(expand 无 prereq)。"""
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            nexus_id = UnitTypeId.NEXUS
            current = len(self._bot.townhalls.ready) + int(self._bot.already_pending(nexus_id))
            target = payload.target_count
        except Exception:
            return
        if current >= target:
            self._set_override_status(
                d, "active", _i18n_t("expand.achieved", self._lang, current=current, target=target)
            )
            return
        # 资源 / mineral check
        try:
            if self._bot.minerals < 400:  # Nexus 需要 400 mineral
                self._set_override_status(
                    d,
                    "on_hold",
                    _i18n_t("expand.needMinerals", self._lang, minerals=self._bot.minerals),
                )
                return
        except Exception:
            pass
        try:
            await self._bot.expand_now()
            logger.info(
                "expansion_override EXPAND (target=%d current=%d, id=%s)",
                target,
                current,
                d.id[:8],
            )
            self._set_override_status(
                d,
                "active",
                _i18n_t("expand.achieved", self._lang, current=current + 1, target=target),
            )
        except Exception as exc:
            logger.debug("expansion_override fail: %s", exc)
            self._set_override_status(d, "on_hold", _i18n_t("expand.failed", self._lang))

    def _release_directive_done(self, d: Directive, now: float, reason: str) -> None:
        """目标达成的 directive 释放(2026-05-23 用户:建造完成 UI 自动消失)。

        2026-05-24 用户:完成不立即删,先 mark status='done' + done_at,5s 后
        on_tick 真删 → 前端这段时间显示"已完成"绿色,然后卡片自然消失。
        """
        from vibecraft.directives.board import BoardEvent, BoardEventKind

        did = d.id
        if self.board.complete(did, now):
            self._dispatch_event(
                BoardEvent(
                    kind=BoardEventKind.RELEASED,
                    ts=now,
                    directive_id=did,
                    reason=reason,
                )
            )
        if self.task_monitor is not None:
            with contextlib.suppress(Exception):
                self.task_monitor.detach(did)
        # 不立即 pop list — 标 done + done_at,on_tick 5s 后清
        self._override_status[did] = {"status": "done", "reason": reason}
        self._done_at[did] = now
        # #4:记终态供历史查询 —— units_lost=已终止,其余=已完成
        self._record_terminal(
            did, "terminated" if reason in ("units_lost", "superseded") else "completed", d
        )
        # 2026-05-24 用户:done 时释放该 directive reserved 的 units
        self._release_standing_order_units(did)
        with contextlib.suppress(Exception):
            self._push_snapshot(now)

    def _count_equivalent(self, type_id: Any) -> tuple[int, int]:
        """返 (ready_count, total_count)，含同质化升级体。

        例：GATEWAY target=8，全部升 WARPGATE 后：
          structures(GATEWAY).ready.amount = 0
          structures(WARPGATE).ready.amount = 8
          → ready=8, total=8 → 不再重复 build。

        total = 所有等价体的 (structures.amount + already_pending) 之和。

        用 type_id.name 查字符串表（_STRUCTURE_EQUIVALENTS_NAMES），避免测试 fake
        导致枚举模块重新加载后对象身份不等的问题。
        """
        # name 查表:type_id.name 是 "GATEWAY" 等字符串,与枚举实例身份无关
        type_name = getattr(type_id, "name", str(type_id))
        alias_names = _STRUCTURE_EQUIVALENTS_NAMES.get(type_name, [type_name])
        # 把字符串名转回 UnitTypeId 枚举(同一 type_id module 实例),失败时用 type_id 本身兜底
        try:
            UnitTypeId = type(type_id)  # 通过传入的 type_id 拿到其 enum class
            aliases = [UnitTypeId[n] for n in alias_names]
        except (KeyError, AttributeError, TypeError):
            aliases = [type_id]
        try:
            ready = sum(self._bot.structures(t).ready.amount for t in aliases)
            total = sum(
                self._bot.structures(t).amount + int(self._bot.already_pending(t)) for t in aliases
            )
        except Exception:
            ready = 0
            total = 0
        return ready, total

    async def _exec_structure_override(self, d: Directive, payload: Any) -> None:
        """L4 建筑目标：遍历 items 逐个 bot.build(structure_id, near=location)。

        多建筑语义:整条 directive 完成 = 所有 item 都达 target_count。
        某 item 卡 on_hold 不阻塞其它 item 继续 build。

        状态语义(2026-05-23 用户:建造中显示"已达成"误导,要求区分):
          all_ready  = 全部 ready_count >= target → reason="已完成"
          all_queued = 全部 ready+pending >= target,但部分还在建 → reason=""(不显示)
          any_active = 本帧有 build 调用 → reason=on_hold 列表
          其它 → on_hold
        """
        from sc2.ids.unit_typeid import UnitTypeId

        on_hold_reasons: list[str] = []
        any_active = False
        all_queued = True  # 全部 ready+pending 满足(已下单,但可能在建)
        all_ready = True  # 全部 ready 完成(真造好)
        for it in payload.items:
            type_name = it.structure_type.upper()
            try:
                type_id = UnitTypeId[type_name]
            except (ImportError, KeyError):
                logger.warning("structure_override 未知 structure %r", it.structure_type)
                on_hold_reasons.append(
                    f"{it.structure_type}: {_i18n_t('struct.unknownBuilding', self._lang)}"
                )
                all_queued = False
                all_ready = False
                continue
            # Q1 fix: 使用同质化计数,避免 GATEWAY→WARPGATE 升级后误判"还需建"
            ready_count, total = self._count_equivalent(type_id)
            if ready_count < it.target_count:
                all_ready = False
            if total >= it.target_count:
                continue  # 已下单(ready 或 pending),不需要再 build
            all_queued = False
            ready, missing = self._check_prereq_ready(type_name)
            if not ready:
                # 2026-05-23 用户:依赖自动补齐扩到 structure_override 自身。
                # 用户说"建 VD"但缺 VC → 自动补 VC 链(不仅停在 on_hold)。
                auto_msg = ""
                try:
                    now_ts = float(getattr(self._bot, "time", 0.0))
                    auto_msg = self._auto_build_prereqs_for(type_name, now_ts)
                except Exception as exc:
                    logger.debug("auto_prereq invocation failed (struct): %s", exc)
                reason = _i18n_t("reason.need", self._lang, missing=missing)
                if auto_msg:
                    reason = _i18n_t("reason.needAuto", self._lang, missing=missing, auto=auto_msg)
                on_hold_reasons.append(f"{it.structure_type}: {reason}")
                continue
            # 挂件(addon)走专门路径:附在空闲父楼上(builder.build(addon)),不是 SCV 盖。
            if self._is_addon_type(type_name):
                issued = await self._build_addon_on_parent(type_id, type_name)
                if issued:
                    logger.info(
                        "structure_override BUILD ADDON %s (ready=%d, total=%d, target=%d, id=%s)",
                        type_id,
                        ready_count,
                        total,
                        it.target_count,
                        d.id[:8],
                    )
                    any_active = True
                else:
                    # 没有空闲且没挂件的父楼 → 等(父楼在产兵/已挂别的件/还没好)
                    on_hold_reasons.append(
                        f"{it.structure_type}: {_i18n_t('struct.waitParent', self._lang)}"
                    )
                continue
            # gas(REFINERY/ASSIMILATOR/EXTRACTOR)必须建在**空闲气泉(geyser)**上:
            # python-sc2 build(gas, near=Point2) 会进 find_placement(地面)分支→失败返 False
            # (gas 分支 assert near is Unit),所以传 Point2 永远建不出气矿(#553 根因:
            #  玩家"补气矿"命令解析对了但 structure_override 用地面点 → 气矿一直不建,total 卡 1)。
            # 改:找一个己方基地上没盖东西的空闲气泉,把 geyser Unit 传给 build。
            if type_name in ("REFINERY", "ASSIMILATOR", "EXTRACTOR"):
                bot = self._bot
                hint_pos = self._resolve_location_hint(it.location_hint, type_id)
                geyser = self._find_free_geyser(hint_pos) if bot is not None else None
                if bot is None or geyser is None:
                    on_hold_reasons.append(
                        f"{it.structure_type}: {_i18n_t('struct.noGeyser', self._lang)}"
                    )
                    continue
                try:
                    await bot.build(type_id, near=geyser)
                    logger.info(
                        "structure_override BUILD GAS %s on geyser=(%.1f,%.1f) (ready=%d, total=%d, target=%d, id=%s)",
                        type_id,
                        geyser.position.x,
                        geyser.position.y,
                        ready_count,
                        total,
                        it.target_count,
                        d.id[:8],
                    )
                    any_active = True
                except Exception as exc:
                    logger.debug("structure_override gas build fail: %s", exc)
                    on_hold_reasons.append(
                        f"{it.structure_type}: {_i18n_t('struct.buildFailed', self._lang)}"
                    )
                continue
            pos = self._resolve_location_hint(it.location_hint, type_id)
            try:
                await self._bot.build(type_id, near=pos)
                logger.info(
                    "structure_override BUILD %s near=%s (ready=%d, total=%d, target=%d, id=%s)",
                    type_id,
                    pos,
                    ready_count,
                    total,
                    it.target_count,
                    d.id[:8],
                )
                any_active = True
            except Exception as exc:
                logger.debug("structure_override build fail: %s", exc)
                on_hold_reasons.append(
                    f"{it.structure_type}: {_i18n_t('struct.buildFailed', self._lang)}"
                )

        # 状态判定(2026-05-23 用户:区分"已完成"vs"建造中"):
        #   all_ready  → 真完成 → release directive(UI 卡片自动消失)
        #   all_queued → 已下单,等建好 → reason 不显示(用户:不要 "已达成" 文字)
        #   any_active → 本帧 build 调用,等下帧再看 → reason on_hold 列表
        #   其它 → on_hold
        if all_ready:
            now_ts = float(getattr(self._bot, "time", 0.0))
            self._release_directive_done(d, now_ts, reason="structure_done")
            return
        if all_queued:
            self._set_override_status(d, "active", "")
        elif any_active:
            self._set_override_status(d, "active", "; ".join(on_hold_reasons))
        else:
            self._set_override_status(d, "on_hold", "; ".join(on_hold_reasons))

    def _exec_drop_act(self, d: Directive, payload: DropActPayload) -> None:
        """L4 drop_act: resolve target → auto_prereq → auto_production → instantiate ActBase.

        步骤:
          1. resolve drop_target spec → DropTarget (失败 → on_hold)
          2. warp_then_drop 时 resolve warp_at (失败 → on_hold)
          3. auto_prereq: 补 cargo_unit + transport 的缺失前置建筑 (复用 _auto_build_prereqs_for)
          4. auto_production: 缺 cargo / transport 时 emit ProductionOverride (防重复)
          5. 单位齐 → 实例化 GenericDropAct / PrismWarpDropAct → 记入 _active_drop_acts
             (防重复:同 directive_id 只实例化一次)

        """
        if self._bot is None:
            return

        from vibecraft.bot.named_spot import NamedSpotRegistry

        reg = NamedSpotRegistry()

        # 1. resolve drop_target
        target = reg.resolve_drop_target(payload.drop_target, self._bot)
        if target is None:
            self._set_override_status(
                d, "on_hold", _i18n_t("drop.targetFailed", self._lang, target=payload.drop_target)
            )
            return

        # 2. warp_at (only for warp_then_drop)
        warp_target = None
        if payload.style == "warp_then_drop":
            if payload.warp_at is None:
                self._set_override_status(d, "on_hold", _i18n_t("drop.needWarpAt", self._lang))
                return
            warp_target = reg.resolve_drop_target(payload.warp_at, self._bot)
            if warp_target is None:
                self._set_override_status(
                    d, "on_hold", _i18n_t("drop.warpAtFailed", self._lang, target=payload.warp_at)
                )
                return

        # 3. auto_prereq: 补 cargo + transport 的前置建筑
        now_ts = float(getattr(self._bot, "time", 0.0))
        for unit_name in (payload.cargo_unit.upper(), payload.transport.upper()):
            try:
                self._auto_build_prereqs_for(unit_name, now_ts)
            except Exception as exc:
                logger.debug("drop_act auto_prereq(%s) fail: %s", unit_name, exc)

        # 4. auto_production: 缺 cargo 或 transport → emit ProductionOverride(防重复)
        self._auto_drop_act_emit_production(d, payload, now_ts)

        # 5. 检查 ready: cargo_count 个 cargo + 1 transport 都 ready → 实例化 ActBase
        if d.id in self._active_drop_acts:
            # 已实例化,不重复
            self._set_override_status(d, "active", _i18n_t("drop.inProgress", self._lang))
            return

        cargo_ready = self._count_ready_units(payload.cargo_unit)
        transport_ready = self._count_ready_units(payload.transport)
        if cargo_ready >= payload.cargo_count and transport_ready >= 1:
            act = self._instantiate_drop_act(payload, target, warp_target)
            if act is not None:
                self._active_drop_acts[d.id] = act
                self._set_override_status(d, "active", _i18n_t("drop.inProgress", self._lang))
            else:
                self._set_override_status(d, "active", _i18n_t("drop.waitRally", self._lang))
        else:
            missing_parts: list[str] = []
            if cargo_ready < payload.cargo_count:
                missing_parts.append(
                    _i18n_t(
                        "drop.waitCargo",
                        self._lang,
                        unit=payload.cargo_unit,
                        ready=cargo_ready,
                        needed=payload.cargo_count,
                    )
                )
            if transport_ready < 1:
                missing_parts.append(
                    _i18n_t("drop.waitTransport", self._lang, unit=payload.transport)
                )
            self._set_override_status(d, "on_hold", "; ".join(missing_parts))

    def _count_ready_units(self, unit_type_name: str) -> int:
        """返回 bot 拥有的指定单位数量 (bot.units(UnitTypeId).amount)。"""
        if self._bot is None:
            return 0
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            uid = UnitTypeId[unit_type_name.upper()]
            return int(self._bot.units(uid).amount)
        except Exception:
            return 0

    def _auto_drop_act_emit_production(
        self, d: Directive, payload: DropActPayload, now_ts: float
    ) -> None:
        """cargo / transport 不足时,emit ProductionOverride 自动出单位 (防重复)。

        _auto_drop_act_emitted 记录已 emit 的 unit name,同 directive 内不重复。
        """
        from vibecraft.directives.models import ProductionItem, ProductionOverridePayload

        def _maybe_emit(unit_name: str, count: int) -> None:
            key = f"{d.id}:{unit_name}"
            if key in self._auto_drop_act_emitted:
                return
            have = self._count_ready_units(unit_name)
            if have >= count:
                return
            needed = count - have
            prod_payload = ProductionOverridePayload(
                items=[ProductionItem(unit_type=unit_name, count=needed)],
            )
            prod_directive = Directive(
                payload=prod_payload,
                issued_at=now_ts,
                source_text=f"auto_drop_act:{d.id}:{unit_name}",
            )
            self._submit_directives([prod_directive], now_ts)
            self._auto_drop_act_emitted.add(key)

        _maybe_emit(payload.cargo_unit, payload.cargo_count)
        _maybe_emit(payload.transport, 1)

    def _instantiate_drop_act(
        self,
        payload: DropActPayload,
        target: Any,
        warp_target: Any | None,
    ) -> Any | None:
        """payload 转 ActBase 实例:simple → GenericDropAct,warp_then_drop → PrismWarpDropAct。

        ActBase 构造失败(import 失败等)时 return None;不抛异常。
        """
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            cargo_uid = UnitTypeId[payload.cargo_unit.upper()]
            transport_uid = UnitTypeId[payload.transport.upper()]
        except Exception as exc:
            logger.warning("drop_act UnitTypeId resolve fail: %s", exc)
            return None

        if payload.style == "simple":
            try:
                from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
                    GenericDropAct,
                )

                return GenericDropAct(
                    cargo_unit=cargo_uid,
                    cargo_count=payload.cargo_count,
                    transport=transport_uid,
                    drop_target=target,
                    after_unload=payload.after_unload,
                )
            except Exception as exc:
                logger.warning("GenericDropAct instantiate fail: %s", exc)
                return None

        # warp_then_drop
        if payload.style == "warp_then_drop":
            try:
                from vibecraft.bot.auto_combat.protoss.plans.prism_warp_drop_act import (
                    PrismWarpDropAct,
                )

                # PrismWarpDropAct 签名: cargo_unit, cargo_count, warp_pos, final_drop_pos
                # warp_target = warp_pos (前线 warp 点), target = final_drop_pos (深入卸下点)
                return PrismWarpDropAct(
                    cargo_unit=cargo_uid,
                    cargo_count=payload.cargo_count,
                    warp_pos=warp_target,
                    final_drop_pos=target,
                    after_unload=payload.after_unload,
                )
            except Exception as exc:
                logger.warning("PrismWarpDropAct instantiate fail: %s", exc)
                return None

        return None

    def _find_free_geyser(self, near: Any) -> Any:
        """找一个空闲气泉(geyser):在己方基地附近、上面没有已建/在建的 gas building。
        near(Point2)非空时返回**就近**的那个;无空闲返回 None。用于气矿(REFINERY)的
        structure_override —— python-sc2 build(gas) 要求传 geyser Unit,不能传地面点。
        """
        bot = self._bot
        if bot is None:
            return None
        try:
            geysers = bot.vespene_geyser
            townhalls = bot.townhalls
            gas_b = bot.gas_buildings
            free = []
            for g in geysers:
                # 必须在某个己方基地附近(~12 格);不在己方基地的气泉不能建
                if townhalls and not townhalls.closer_than(12.0, g).exists:
                    continue
                # 气泉上已有(已建或在建)gas building → 占用,跳过
                if gas_b and gas_b.closer_than(1.5, g).exists:
                    continue
                free.append(g)
            if not free:
                return None
            if near is not None:
                return min(free, key=lambda g: g.distance_to(near))
            return free[0]
        except Exception:
            return None

    def _resolve_location_hint(self, hint: str | None, type_id: Any) -> Any:
        """hint(None/main/natural/ramp/front) → 合理的 Point2(远离矿区)。

        2026-05-28 广义 A:hint 是「区域」不是「精确点」。除 front 外都从 sharpy
        `IBuildingSolver` 预计算 grid 挑空位(sharpy plan 内 `GridBuilding` act
        用同一份),避开矿区 / 采矿路径。

        | hint | 行为 |
        |---|---|
        | `None` | grid 第一个空位(默认排序,贴主基地外侧) |
        | `"main"` | grid 中距 `zones[0].center_location` 最近的空位 |
        | `"natural"` | grid 中距 `zones[1].center_location` 最近的空位 |
        | `"ramp"` | grid 中距 `main_base_ramp.top_center` 最近的空位 |
        | `"front"` | `enemy_main_base_ramp.top_center`(proxy 用,刻意远离 grid)|

        grid 不可用(`building_solver` 没就绪 / 接口变了 / 早期 game)→ fallback
        到旧路径(`zones[X].center_location` 或 `own_main`),保留 2026-05-27
        Issue C 防 None 炸的兜底。

        历史:Issue C 修前 `hint=None → None`,bot.build(near=None) 内部 assert
        被 except 吞,玩家"出 1 个 VR" 无反应。Issue C 修法返 `own_main
        center_location`(矿区中心),建筑都堆主基地周围/矿区。广义 A 才是正确语义。
        """
        try:
            zones = self._bot.knowledge.zone_manager.expansion_zones
        except Exception:
            zones = []

        def _zone_gather(zone: Any) -> Any:
            """优先 gather_point(Nexus 后院,远离矿线),fallback center_location。"""
            try:
                return zone.gather_point
            except AttributeError:
                return zone.center_location

        def _own_main_fallback() -> Any:
            if zones:
                # Q2 fix: 用 gather_point 而非 center_location(center_location 在矿区中心,
                # 会把建筑落到 Nexus↔mineral 通道,挡 probe 采矿路径)。
                return _zone_gather(zones[0])
            try:
                return self._bot.townhalls.first.position
            except Exception:
                try:
                    return self._bot.start_location
                except Exception:
                    return None

        # 各 hint 决定锚点(从 grid 中挑距锚点最近的空位);None = 不限定,取第一个
        anchor: Any = None
        legacy_fallback: Any = None  # grid 不可用时用的旧 Point2(防退化到 None)

        if hint is None:
            anchor = None
            legacy_fallback = _own_main_fallback()
        elif hint == "main":
            # Q2 fix: gather_point = Nexus 后院,远离矿区;原 center_location 会落在矿线
            anchor = _zone_gather(zones[0]) if zones else None
            legacy_fallback = anchor or _own_main_fallback()
        elif hint == "natural":
            anchor = _zone_gather(zones[1]) if len(zones) > 1 else None
            legacy_fallback = anchor or _own_main_fallback()
        elif hint == "ramp":
            try:
                anchor = self._bot.main_base_ramp.top_center
            except Exception:
                anchor = None
            legacy_fallback = anchor or _own_main_fallback()
        elif hint == "front":
            # front = proxy 用(敌方 ramp),刻意远离己方 grid;不走 building_solver
            try:
                return self._bot.knowledge.enemy_main_base_ramp.top_center
            except Exception:
                return _own_main_fallback()
        else:
            # 未知 hint → 兜底 own_main(防 _bot.build 炸)
            return _own_main_fallback()

        # 从 sharpy grid 挑「距 anchor 最近的空位」(anchor=None 取第一个)
        pos = self._pick_grid_position(type_id, anchor=anchor)
        if pos is not None:
            return pos
        return legacy_fallback

    # 2x2 建筑(占小格的)— 三族共用清单
    # protoss: PYLON, terran: SUPPLYDEPOT, zerg: 无 2x2(creep 上 3x3)
    _2X2_BUILDINGS: ClassVar[set[str]] = {"PYLON", "SUPPLYDEPOT"}

    def _pick_grid_position(self, type_id: Any, anchor: Any = None) -> Any:
        """从 sharpy `IBuildingSolver` 预计算 grid 列表挑「距 anchor 最近的空位」。

        跟 sharpy `GridBuilding` act 的 `position_protoss/zerg/terran` 选址逻辑
        一致(vendor/sharpy/sharpy/plans/acts/grid_building.py:254):预计算远离
        矿区、沿出口侧的 grid 位置,逐点 check `not buildings.closer_than(1, p)`
        过滤已占用的。

        - `anchor=None`:返第一个空位(grid 默认排序通常贴主基地外侧)
        - `anchor=Point2`:返「距 anchor 最近的空位」(实现 hint=main/natural/ramp
          的「区域」语义 — 玩家说"放主基地"挑主基地区内的合理 grid)

        失败(`building_solver` 没初始化 / 接口变了 / grid 全占满)→ None,
        让 caller fallback。
        """
        try:
            from sharpy.interfaces import IBuildingSolver
        except ImportError:
            return None
        try:
            solver = self._bot.knowledge.get_required_manager(IBuildingSolver)
        except Exception as exc:
            logger.debug("building_solver lookup fail: %s", exc)
            return None
        try:
            type_name = type_id.name if hasattr(type_id, "name") else str(type_id)
            is_2x2 = type_name.upper() in self._2X2_BUILDINGS
            grid = solver.buildings2x2 if is_2x2 else solver.buildings3x3
            buildings = self._bot.structures
            candidates = [p for p in grid if not buildings.closer_than(1, p)]
            if not candidates:
                return None
            if anchor is None:
                return candidates[0]
            # 距 anchor 最近的空位:实现 hint=main/natural/ramp 的「区域」语义
            return min(candidates, key=lambda p: p.distance_to(anchor))
        except Exception as exc:
            logger.debug("grid position pick fail: %s", exc)
        return None

    # 兵种名归一:别名 canonical 名 → 真·可训练的 UnitTypeId 名。
    # 踩坑(2026-06-17 真局):玩家"出维京"永不出兵 —— 别名 canonical 是 "Viking",
    # 但 UnitTypeId["VIKING"]=1940 是**不可训练的占位 enum**(trained_from=None),
    # 真·可训练的飞行模式维京是 VIKINGFIGHTER(35, from STARPORT)→ bot.train(1940)
    # 静默 no-op。其余双形态/占位单位的 canonical 名都直接落在可训练 enum 上
    # (HELLION/SIEGETANK/WIDOWMINE/LIBERATOR/THOR 均 OK),目前只有 VIKING 这一例。
    _UNIT_NAME_MAP: ClassVar[dict[str, str]] = {
        "VIKING": "VIKINGFIGHTER",
    }

    @classmethod
    def _resolve_unit_type_id(cls, name: str) -> Any:
        """字符串 'Sentry' → UnitTypeId.SENTRY。失败返回 None。

        先过 _UNIT_NAME_MAP 把"别名 canonical 名落在不可训练占位 enum 上"的兵种
        (VIKING→VIKINGFIGHTER)归一到真·可训练 enum,再查 UnitTypeId。
        """
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            key = name.upper()
            key = cls._UNIT_NAME_MAP.get(key, key)
            return UnitTypeId[key]
        except (ImportError, KeyError):
            return None

    # 挂件(addon)类型 → 父楼类型名。挂件附在父楼上(不是 SCV 盖),要 builder.build(addon)。
    # 2026-06-17 用户:① "重工下科技挂件"被误当建新重工 ② 出坦克没自动补科技挂件。
    _ADDON_PARENTS: ClassVar[dict[str, str]] = {
        "BARRACKSTECHLAB": "BARRACKS",
        "BARRACKSREACTOR": "BARRACKS",
        "FACTORYTECHLAB": "FACTORY",
        "FACTORYREACTOR": "FACTORY",
        "STARPORTTECHLAB": "STARPORT",
        "STARPORTREACTOR": "STARPORT",
    }

    # Reactor-friendly (mass-mineral) 兵种:双倍挂件让该楼同时造 2 个。
    # 2026-06-18 P1 addon decision:有这类兵在需求集里时 reactor >= 1。
    _ADDON_REACTOR_UNITS: ClassVar[frozenset[str]] = frozenset(
        {
            "MARINE",
            "HELLION",
            "HELLIONTANK",
            "WIDOWMINE",
            "MEDIVAC",
            "VIKINGFIGHTER",
            "VIKINGASSAULT",
        }
    )

    # 产能建筑 → 挂件名对(TechLab, Reactor)
    _ADDON_PAIR: ClassVar[dict[str, tuple[str, str]]] = {
        "BARRACKS": ("BarracksTechLab", "BarracksReactor"),
        "FACTORY": ("FactoryTechLab", "FactoryReactor"),
        "STARPORT": ("StarportTechLab", "StarportReactor"),
    }

    @classmethod
    def _is_addon_type(cls, type_name: str) -> bool:
        return type_name.upper() in cls._ADDON_PARENTS

    async def _build_addon_on_parent(self, addon_type_id: Any, type_name: str) -> bool:
        """给一个没挂件的父楼挂挂件，挂件位被占时**起飞→找空位→落下→再挂**。

        挂件(addon)附在父楼右侧 2x2。流程（每帧推进一步，多帧完成）：
          1. 父楼落地 + idle + 当前位置有挂件空位 → `builder.build(addon)`。
          2. 父楼落地 + idle + **挂件位被占**（没空位）→ 先确认附近有"楼3x3+挂件2x2"
             都放得下的落点，有才**起飞**(LIFT)；没有就跳过（别瞎飞）。
          3. 父楼**在飞**（已起飞挪位中）→ 找带挂件空位的落点 → **落下**(LAND)。
             落地后下一帧回到步骤 1 挂上。
        2026-06-17 用户：挂件位被占时要算好空间再起飞挪位再挂。每帧只动一座楼。
        返回是否本帧下了令（挂/起飞/落）。
        """
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        parent_name = self._ADDON_PARENTS.get(type_name.upper())
        if parent_name is None or self._bot is None:
            return False
        try:
            parent_id = UnitTypeId[parent_name]
            lift_ability = AbilityId[f"LIFT_{parent_name}"]
            land_ability = AbilityId[f"LAND_{parent_name}"]
        except KeyError:
            return False
        # 父楼一起飞就变成 <PARENT>FLYING(另一个 UnitTypeId)——structures(parent_id) 不含飞行
        # 变体。若只遍历落地变体,LIFT 后这座楼从循环消失 → 永远等不到 LAND(2026-06-17 真局坐实)。
        # 把飞行变体也纳入遍历,起飞→落下闭环才走得通(三族通用:FACTORY/BARRACKS/STARPORTFLYING)。
        flying_id = None
        try:
            flying_id = UnitTypeId[f"{parent_name}FLYING"]
        except KeyError:
            flying_id = None

        async def _has_addon_space(b: Any) -> bool:
            """父楼当前位置右侧 2x2 挂件位是否空（用 SUPPLYDEPOT 2x2 footprint 探）。"""
            try:
                center = b.position.offset(Point2((2.5, -0.5)))
                res = await self._bot.find_placement(
                    UnitTypeId.SUPPLYDEPOT, center, max_distance=0, random_alternative=False
                )
                return res is not None
            except Exception:
                return False

        async def _find_relocate_spot(b: Any) -> Any:
            """由近及远**确定性网格扫描**,找'楼3x3+右侧挂件2x2 都放得下'的落点。

            两个坑都规避(2026-06-17 真局自验坐实):
            - **不**用 `find_placement(addon_place=True)`：它网格分支的挂件位检查走
              `TERRANBUILDDROP_SUPPLYDEPOTDROP` query,对地形过度严格——明明 can_place_single
              说挂件位放得下、它却恒 None → 挂件位被占时即使有空地也挪不动。
            - **不**用 `find_placement(random_alternative=True)` 多次采样:楼一起飞原地面空出,
              随机采样老采到原点附近(挂件位仍被堵),采不到远处那个合法点 → 飞起来却落不下。
            改用 `can_place_single` 双验(楼 + 右侧挂件),由近及远逐环扫,第一个双过的就用。
            """
            base = b.position
            try:
                for radius in range(2, 17, 2):
                    for dx in range(-radius, radius + 1, 2):
                        for dy in range(-radius, radius + 1, 2):
                            if max(abs(dx), abs(dy)) != radius:
                                continue  # 只扫当前环(由近及远)
                            p = base.offset(Point2((float(dx), float(dy))))
                            if await self._bot.can_place_single(
                                parent_id, p
                            ) and await self._bot.can_place_single(
                                UnitTypeId.SUPPLYDEPOT, p.offset(Point2((2.5, -0.5)))
                            ):
                                return p
                return None
            except Exception:
                return None

        # 落点缓存(tag → Point2):起飞前就基于**稳定的地面位置**定好落点,飞行中一直发同一个,
        # 别每帧拿漂移中的飞行位置重算 → 否则楼追移动目标、抽搐落不下(2026-06-17 真局坐实)。
        land_targets = getattr(self, "_addon_land_targets", None)
        if land_targets is None:
            land_targets = {}
            self._addon_land_targets = land_targets
        try:
            builders = self._bot.structures(parent_id).ready
            if flying_id is not None:
                builders = builders + self._bot.structures(flying_id).ready
            for builder in builders:
                tag = int(builder.tag)
                if int(getattr(builder, "add_on_tag", 0)) != 0:
                    land_targets.pop(tag, None)
                    continue  # 已经有挂件了
                if getattr(builder, "is_flying", False):
                    # 在飞 → 用起飞前定好的落点(没有才临时算一个),反复发同一个直到落地
                    target = land_targets.get(tag)
                    if target is None:
                        target = await _find_relocate_spot(builder)
                    if target is not None:
                        land_targets[tag] = target
                        builder(land_ability, target)
                        logger.info("addon relocate: LAND %s @ %s", parent_name, target)
                        return True
                    continue  # 暂没落点,继续飞着等下帧
                # 落地状态
                land_targets.pop(tag, None)  # 已落地,清缓存
                if not getattr(builder, "is_idle", True):
                    continue  # 产兵中的楼不动（不打断生产去挂件）
                if await _has_addon_space(builder):
                    builder.build(addon_type_id)
                    return True
                # 挂件位被占 → 先把落点算好缓存,有才起飞挪位(落点基于稳定地面位置)
                spot = await _find_relocate_spot(builder)
                if spot is not None:
                    land_targets[tag] = spot
                    builder(lift_ability)
                    logger.info("addon relocate: LIFT %s (挂件位被占) → 落点 %s", parent_name, spot)
                    return True
        except Exception as exc:
            logger.debug("build addon on parent fail: %s", exc)
        return False

    @staticmethod
    def _unit_requires_techlab(unit_id: Any) -> bool:
        """该兵种是否需要生产建筑挂 TechLab（坦克/掠夺者/女妖/雷神/渡鸦/BC/幽灵/机枪车）。

        数据源 python-sc2 `TRAIN_INFO[parent][unit]['requires_techlab']`，Blizzard 维护。
        """
        try:
            from sc2.dicts.unit_train_build_abilities import TRAIN_INFO

            for _parent, children in TRAIN_INFO.items():
                info = children.get(unit_id)
                if info is not None and info.get("requires_techlab"):
                    return True
        except Exception:
            return False
        return False

    def _producer_has_techlab(self, unit_id: Any) -> bool:
        """该兵种的生产建筑里，是否已有至少一个挂了 TechLab（够它出兵）。"""
        if self._bot is None:
            return False
        try:
            from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM

            producers = UNIT_TRAINED_FROM.get(unit_id, set())
            for parent_id in producers:
                for b in self._bot.structures(parent_id).ready:
                    if getattr(b, "has_techlab", False):
                        return True
        except Exception:
            return False
        return False

    def _techlab_addon_name_for(self, unit_id: Any) -> str | None:
        """该兵种的生产建筑对应的 TechLab 挂件 enum 名（FACTORY→FACTORYTECHLAB 等）。"""
        try:
            from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM

            for parent_id in UNIT_TRAINED_FROM.get(unit_id, set()):
                name = f"{parent_id.name}TECHLAB"
                if name in self._ADDON_PARENTS:
                    return name
        except Exception:
            return None
        return None

    # L4 override 的 prereq structure 表(canonical unit/upgrade name → required structure)。
    # train Sentry 前 Cybernetics Core 要 ready;研究 Blink 前 Twilight Council 要 ready。
    # None 表示无 prereq(如 Zealot / Archon 合成)。
    # 不在表里的 unit(如 Probe)默认无 prereq。
    # 2026-05-23 用户:依赖自动补齐扩到三族 → 删手写 _REQUIRED_STRUCTURE 表,
    # 改用 vibecraft.bot.tech_tree.required_for(走 python-sc2 内置三族
    # TECH_REQUIREMENT + UPGRADE_RESEARCHED_FROM)。所有 _check_prereq_ready
    # 调用走 tech_tree,三族通用零维护。

    # LLM payload upgrade_id (跟 strategies/aliases yaml 一致) → sc2 UpgradeId enum
    # python-sc2 enum 名比 strategies yaml canonical id 多带 "TECH" / "LEVEL" 等后缀。
    _UPGRADE_NAME_MAP: ClassVar[dict[str, str]] = {
        # Twilight
        "BLINK": "BLINKTECH",
        "CHARGE": "CHARGE",
        "RESONATINGGLAIVES": "ADEPTPIERCINGATTACK",
        "GLAIVE": "ADEPTPIERCINGATTACK",
        # Templar Archives
        "PSISTORM": "PSISTORMTECH",
        # Cybernetics
        "WARPGATERESEARCH": "WARPGATERESEARCH",
        "WARPGATE": "WARPGATERESEARCH",
        # Forge — 分 3 级,默认 level1
        "PROTOSSGROUNDWEAPONS": "PROTOSSGROUNDWEAPONSLEVEL1",
        "PROTOSSGROUNDARMOR": "PROTOSSGROUNDARMORSLEVEL1",
        "PROTOSSSHIELDS": "PROTOSSSHIELDSLEVEL1",
        # Fleet Beacon / Cybernetics
        "PROTOSSAIRWEAPONS": "PROTOSSAIRWEAPONSLEVEL1",
        "PROTOSSAIRARMOR": "PROTOSSAIRARMORSLEVEL1",
        # Tempest
        "TEMPESTRANGE": "TEMPESTRANGEUPGRADE",
        "TEMPESTGROUND": "TEMPESTGROUNDATTACKUPGRADE",
        # 人族 Fusion Core — 大和炮(武器改装),enum 名是 BATTLECRUISERENABLESPECIALIZATIONS
        "YAMATO": "BATTLECRUISERENABLESPECIALIZATIONS",
        "WEAPONREFIT": "BATTLECRUISERENABLESPECIALIZATIONS",
    }

    @classmethod
    def _resolve_upgrade_id(cls, name: str) -> Any:
        """字符串 → UpgradeId enum。先查 _UPGRADE_NAME_MAP,fallback 直接 enum["NAME"]。"""
        try:
            from sc2.ids.upgrade_id import UpgradeId
        except ImportError:
            return None
        up = name.upper()
        mapped = cls._UPGRADE_NAME_MAP.get(up, up)
        try:
            return UpgradeId[mapped]
        except KeyError:
            return None

    def _production_override_built_count(self, directive: Directive, unit_type: str) -> int:
        """查 task_monitor 累计的 unit_count_built_since per-type counter。

        task_monitor._unit_built_counts[did][unit_type] 由 EventBus UNIT_CREATED
        handler 维护（per-unit_type 计数；多兵种 directive 各 item 独立）。
        没 done_when 时 fallback 返回 0(每 tick 都试 train)。
        """
        if self.task_monitor is None:
            return 0
        try:
            counts_by_type = self.task_monitor._unit_built_counts.get(directive.id, {})
            return int(counts_by_type.get(unit_type, 0))
        except Exception:
            return 0

    def _remember_command(self, text: str, now: float, outcome: ParseOutcome | None = None) -> None:
        summary = self._summarize_outcome(outcome) if outcome is not None else None
        # #4:抓 interpretation_zh + directive_ids(给历史三层展开关联卡片)
        interp = ""
        dir_ids: list[str] = []
        if isinstance(outcome, IntentParseResult):
            interp = outcome.interpretation_zh or ""
            dir_ids = [d.id for d in outcome.directives]
        elif isinstance(outcome, AmbiguousParse):
            interp = outcome.result.interpretation_zh or ""
        # 识别失败：有 outcome 但既不是成功解析也不是模糊澄清（= ParseError）→ 历史标红。
        failed = outcome is not None and not isinstance(
            outcome, (IntentParseResult, AmbiguousParse)
        )
        self._recent_commands.append(
            _RecentCommand(
                text=text,
                ts=now,
                outcome_summary=summary,
                interpretation_zh=interp,
                directive_ids=dir_ids,
                failed=failed,
            )
        )
        # buffer > 0 → 保留最近 N 句;buffer = 0(default 2026-05-24) → 整局不限
        buf = self.config.recent_command_buffer
        if buf > 0 and len(self._recent_commands) > buf:
            self._recent_commands.pop(0)

    def _summarize_outcome(self, outcome: ParseOutcome) -> str:
        """把 ParseOutcome 压成一行摘要(给 LLM 看的局内 memory)。

        例:
          - "strategy_set(stage=midgame, strategy_id=iac_2base) id=d_a3f1c2"
          - "unit_claim(Probe patrol natural, persistent=true) id=d_8b2d4e"
          - "[parse error: schema validation]"
          - "[ambiguous: 哪个剧本?]"
        """
        if isinstance(outcome, ParseError):
            return f"[parse error: {outcome.message[:60]}]"
        if isinstance(outcome, AmbiguousParse):
            interp = outcome.result.interpretation_zh[:60]
            return f"[ambiguous: {interp}]"
        if isinstance(outcome, IntentParseResult):
            if not outcome.directives:
                return "[empty: no directives]"
            return " | ".join(self._brief_directive(d) for d in outcome.directives)
        return "[unknown outcome]"

    def _brief_directive(self, d: Directive) -> str:
        """单条 directive 关键字段摘要(给 LLM 看,不是 JSON)。"""
        p = d.payload
        t = d.type.value
        sid = d.id[:8]
        parts: list[str] = []
        if isinstance(p, StrategySetPayload):
            parts.append(f"stage={p.stage} id={p.strategy_id}")
        elif isinstance(p, ProductionOverridePayload):
            parts.append(",".join(f"{it.unit_type}×{it.count}" for it in p.items))
        elif isinstance(p, TechOverridePayload):
            parts.append(f"upgrade={p.upgrade_id}")
        elif isinstance(p, ExpansionOverridePayload):
            parts.append(f"target_count={p.target_count}")
        elif isinstance(p, EngagementConstraintPayload):
            parts.append(f"stance={p.stance}")
        elif isinstance(p, UnitClaimPayload):
            unit = p.selector.unit_type or "?"
            verb = p.task.primary_action.verb.value
            target = p.task.primary_action.target
            # target 可为 None（如 verb=harass_workers 不指定矿区 → auto 轮换找有农民的敌矿）。
            tgt = (target.named_spot or target.unit_type or "?") if target is not None else "auto"
            persist = ", persistent=true" if p.persistent else ""
            parts.append(f"{unit} {verb} {tgt}{persist}")
        elif isinstance(p, ScoutPayload):
            unit = p.selector.unit_type if p.selector else "?"
            tgt = p.target.named_spot or "?"
            parts.append(f"{unit}→{tgt}")
        elif isinstance(p, MovePayload):
            unit = p.selector.unit_type or "?"
            tgt = p.target.named_spot or "?"
            parts.append(f"{unit}→{tgt}")
        elif isinstance(p, BuildAtPayload):
            parts.append(f"{p.structure_type}@{p.point}")
        elif isinstance(p, UnitReleasePayload):
            unit = p.selector.unit_type or "?"
            parts.append(f"release {unit}")
        body = ", ".join(parts) if parts else ""
        return f"{t}({body}) id={sid}"

    # ------------------------------------------------------------------
    # Task #311 player override e2e: scheduled player action 触发
    # ------------------------------------------------------------------

    def _fire_scheduled_action(self, idx: int, action: dict[str, Any], now: float) -> None:
        """模拟玩家按 UI 战术按钮(对齐 common_bot._submit_tactical_action 行为)。

        - mode 在 submit_directive 前先 facade.set_attack_mode_override:防 ZoneAttack
          同帧读到 intent=attack 但 mode 还没设导致 force_attack 失效
        - persistent=True:让 facade combat_intent_override 持续生效,跟 UI 按钮一致
        - issued_by=VOICE + source_text="e2e scheduled":telemetry 里能区分自动 vs 手动
        - _fired_player_actions add idx:防同 action 多 tick 重触
        """
        from vibecraft.directives.models import Directive, TacticalObjectivePayload
        from vibecraft.directives.types import IssuedBy

        verb = str(action["verb"])
        mode = action.get("mode")
        target_area = action.get("target_area")

        # mode 必须在 submit 前 set(对齐 common_bot 注释里的同帧 race 修复)
        if mode in ("all_in", "probe") and self.facade is not None:
            set_mode = getattr(self.facade, "set_attack_mode_override", None)
            if set_mode is not None:
                set_mode(mode)

        payload = TacticalObjectivePayload(
            verb=verb,  # type: ignore[arg-type]
            target_area=target_area,
            persistent=True,
            attack_mode=mode if mode in ("all_in", "probe") else None,  # type: ignore[arg-type]
        )
        source = f"e2e scheduled: {verb}"
        if mode:
            source = f"{source} mode={mode}"
        directive = Directive(
            payload=payload,
            issued_at=now,
            issued_by=IssuedBy.VOICE,
            source_text=source,
        )
        self.submit_directive(directive, now)
        self._fired_player_actions.add(idx)
        logger.info(
            "e2e_player_action_fired idx=%d verb=%s mode=%s target=%s at=%.1f",
            idx,
            verb,
            mode,
            target_area,
            now,
        )

    # ------------------------------------------------------------------
    # 每 tick
    # ------------------------------------------------------------------

    def on_tick(self, now: float) -> list[BoardEvent]:
        # 顶部 import 避免局部 scope 撞掉 module-level reference
        from vibecraft.directives.board import BoardEvent, BoardEventKind

        # 2026-06-15 build 效率沙盒:强制全程 defend,bot 只 macro 不主动 moveout,隔离战斗损耗。
        # 每 tick 幂等重设(防被别的逻辑清掉);仅纯运营 build 评测时开。set_*_override 是
        # knowledge.vibecraft 持久字段,sharpy combat hook 每帧读 → intent=defend 不出门。
        if self._sandbox_macro_only:
            with contextlib.suppress(Exception):
                self.facade.set_combat_intent_override("defend")
                self.facade.set_engagement_stance("defend")

        # Task #311 player override e2e: 到点 fire 玩家时间线项,模拟 UI 按钮。
        # 必须在 board.tick() 之前,这样 fire 内的 submit_directive (commit_delay=0)
        # 即时进 board._pending → board.tick() 把 COMMITTED event 返出来 →
        # 同 on_tick 内 dispatch 到 facade。
        if self._scheduled_player_actions:
            for idx, action in enumerate(self._scheduled_player_actions):
                if idx in self._fired_player_actions:
                    continue
                if now >= float(action["at_s"]):
                    self._fire_scheduled_action(idx, action, now)

        events = self.board.tick(now)
        need_snapshot = False
        for ev in events:
            self._dispatch_event(ev)
            # 变化推：strategy 变化时立即推 snapshot（P0-2）
            if ev.kind in (BoardEventKind.STRATEGY_CHANGED, BoardEventKind.PHASE_TRANSITIONED):
                need_snapshot = True

        # P3.2: task_monitor 检查 done
        if self.task_monitor is not None:
            game_state = getattr(self, "_bot", None)
            completed_ids = self.task_monitor.tick(now, game_state=game_state)
            for did in completed_ids:
                if self.board.complete(did, now):
                    # board.complete fires RELEASED into board._events,
                    # 但 board.tick() return 已经过去(只含本 tick produced),
                    # board._events 累积要等下次 tick 才被 drain。
                    # 直接 dispatch RELEASED 让 events/directives.jsonl 立即落盘。
                    self._dispatch_event(
                        BoardEvent(
                            kind=BoardEventKind.RELEASED,
                            ts=now,
                            directive_id=did,
                            reason="task_monitor_done",
                        )
                    )
                self.task_monitor.detach(did)
                # 2026-05-24 用户:完成不立即删,先 mark done + done_at,
                # 前端显示"已完成"5s 后真删 → 卡片自然消失。
                self._override_status[did] = {
                    "status": "done",
                    "reason": _i18n_t("reason.done", self._lang),
                }
                self._done_at[did] = now
                need_snapshot = True

        # 2026-05-24 真删 grace 期已过的 done directives
        expired = [did for did, t in self._done_at.items() if now - t > self._DONE_GRACE_S]
        for did in expired:
            self._in_flight.pop(did, None)
            # 2026-05-25 bug 5:清掉 commit 后保留的 ephemeral directive
            self._committed_directives.pop(did, None)
            self.production_overrides = [d for d in self.production_overrides if d.id != did]
            # #3 用户:持久指令(L3)done grace 过后也要从 standing_orders 删 → 卡片消失。
            # 修前 standing_orders 不在此清理 → 单位全失卡片永久暗红不消失。
            self.standing_orders = [d for d in self.standing_orders if d.id != did]
            self._override_status.pop(did, None)
            self._production_item_status.pop(did, None)
            self._done_at.pop(did, None)
            # 出兵集结点卡过期(被覆盖/× grace 过)→ 若是当前 rally 则清状态(停每帧续设)
            if did == self._rally_point_id:
                self._rally_point = None
                self._rally_point_id = None
                with contextlib.suppress(Exception):
                    self.facade.set_rally_point(None)
            # 兜底:释放任何剩余 reserved units(_release_directive_done 应已释)
            self._release_standing_order_units(did)
            need_snapshot = True

        # #3 用户:持久指令(L3)认领单位全死 → 自动终止(units_lost),卡片暗红后消失
        try:
            self._tick_standing_order_deaths(now)
        except Exception as exc:
            logger.debug("_tick_standing_order_deaths fail: %s", exc)

        # 2026-05-24 用户:STANDBY 待命指令每 tick 控位
        try:
            self._tick_standby_orders()
        except Exception as exc:
            logger.debug("_tick_standby_orders fail: %s", exc)

        # 2026-06-07 出兵集结点:每 tick 续设 sharpy gather_point(一次性 flag,不每帧重设
        # 会被 _find_gather_point 重算回默认)。_rally_point=None(未设/已×)时不调,恢复默认。
        if self._rally_point is not None:
            with contextlib.suppress(Exception):
                self.facade.set_rally_point(self._rally_point)

        # 2026-06-13 持续征兵:每 tick 把新出现的匹配单位并入编队/standing order
        try:
            self._tick_recruit_watchers(now)
        except Exception as exc:
            logger.debug("_tick_recruit_watchers fail: %s", exc)

        # 2026-05-24 safe_move:走 plan_drop_path 顺序 move,到达 target 触发 done
        try:
            self._tick_safe_move_orders(now)
        except Exception as exc:
            logger.debug("_tick_safe_move_orders fail: %s", exc)

        # 2026-05-28 用户:activate_when 激活门每 tick re-check
        try:
            self._tick_pending_activation(now)
        except Exception as exc:
            logger.debug("_tick_pending_activation fail: %s", exc)

        # 2026-05-27 Issue 3:pending_move 每 tick re-resolve selector,等 unit
        try:
            self._tick_pending_move(now)
        except Exception as exc:
            logger.debug("_tick_pending_move fail: %s", exc)

        # 2026-06-01 Task F:巡逻两点无限往返
        try:
            self._tick_patrol(now)
        except Exception as exc:
            logger.debug("_tick_patrol fail: %s", exc)

        # 2026-06-06 代理建造状态机:持有建造农民直到建好/超时再放归(防被 bot 拉扯)
        try:
            self._tick_proxy_build(now)
        except Exception as exc:
            logger.debug("_tick_proxy_build fail: %s", exc)

        # 2026-06-19 地堡回收预备队：先卸载再拆（SC2 拒绝拆带兵地堡）
        try:
            self._tick_pending_salvage(now)
        except Exception as exc:
            logger.debug("_tick_pending_salvage fail: %s", exc)

        # 2026-06-19 通用维修指令：持续派 SCV 维修目标，满血/消失后自动完成
        try:
            self._tick_repair_orders(now)
        except Exception as exc:
            logger.debug("_tick_repair_orders fail: %s", exc)

        # 2026-07-08 农民基地调度 transfer_to_base：settle 期内持续钉住,到期释放
        try:
            self._tick_worker_task_transfer(now)
        except Exception as exc:
            logger.debug("_tick_worker_task_transfer fail: %s", exc)

        # 2026-06-29 #580 BC 群骚扰：bc_rush 开局自动建 group_harass claim + 发布 bc_harass_groups
        try:
            self._tick_bc_group_harass(now)
        except Exception as exc:
            logger.debug("_tick_bc_group_harass fail: %s", exc)

        # 2026-07-05 harass_workers player claim：发布 tags + 驱动 hit-and-run 微操
        try:
            self._tick_worker_harass()
        except Exception as exc:
            logger.debug("_tick_worker_harass fail: %s", exc)

        # 2026-06-10 WP2 偷矿:StealthCellManager 每 tick 驱动所有 cell 状态机
        # WP2 实现 PENDING→BUILDING→MINING（代理建造 + Nexus settle 注册 FENCE）
        try:
            self._stealth_manager.on_tick(self._bot, self.facade, now)
        except Exception as exc:
            logger.debug("_stealth_manager.on_tick fail: %s", exc)
        # drain pending_release_events：cell 被 release/destroy 时推 event 给 PWA
        # 同时清 _committed_directives / _cell_id_to_directive_id / _directive_to_cell_id
        if self._stealth_manager.pending_release_events:
            for rev_ev in self._stealth_manager.pending_release_events:
                cid = rev_ev["cell_id"]
                did = self._cell_id_to_directive_id.pop(cid, None)
                if did is not None:
                    self._directive_to_cell_id.pop(did, None)
                    self._committed_directives.pop(did, None)
                    self._override_status.pop(did, None)
                reason_zh = (
                    _i18n_t("salvage.underAttack", self._lang)
                    if rev_ev.get("reason") == "under_attack"
                    else _i18n_t("salvage.destroyed", self._lang)
                )
                self._push_event(
                    {
                        "type": "event",
                        "kind": "stealth.cell_released",
                        "ts": round(now, 3),
                        "payload": {
                            "cell_id": cid,
                            "reason": rev_ev.get("reason", ""),
                            "reason_zh": reason_zh,
                            "location": rev_ev.get("location", []),
                            "state": rev_ev.get("state", ""),
                        },
                    }
                )
            self._stealth_manager.pending_release_events.clear()

        # 2026-05-30 镜头跟随：每 tick 更新 follow 目标
        try:
            self._tick_view_follow(now)
        except Exception as exc:
            logger.debug("_tick_view_follow fail: %s", exc)

        # 2026-05-30 凤凰骚扰卡硬性截止：到点自动收卡 + 凤凰归队主力
        if self._phoenix_harass is not None and now >= self._phoenix_harass["deadline"]:
            self._end_phoenix_harass(now, reason="deadline")

        # 2026-05-27 Task #341: opening sustain uncap 超时检查。
        # opening 完成 + 120s 后,若玩家未切 persistent doctrine → 自动解锁 cap。
        # 2026-06-15: build-aware build(声明 core_units)→ delay 0,opening 一完成立即接管产能扩张
        # (否则 +120s 太晚,实测 bio_stim 8 兵营 725s 才到、人口已满 → 余钱已堆 5000)。
        _has_core_units = self._active_build_has_core_units()
        _effective_delay = 0.0 if _has_core_units else _SUSTAIN_UNCAP_DELAY_S
        _normal_ready = (
            self._opening_completed_signaled
            and self._opening_completed_at is not None
            and now - self._opening_completed_at >= _effective_delay
        )
        # 2026-06-15 兜底：build-aware build 的 opening_completed 信号可能永不 fire
        # （如 reaper_expand 的 _opening_done 要 ≥4 reaper，沙盒里凑不齐 → sustain 永不接管 →
        #  余钱爆 8958）。到 _SUSTAIN_FALLBACK_S 强制 kick，保证 build-aware sustain 总会接管。
        _fallback_ready = _has_core_units and now >= _SUSTAIN_FALLBACK_S
        if not self._sustain_uncap_triggered and (_normal_ready or _fallback_ready):
            # 判断玩家是否已切 persistent doctrine:board.slots[MIDGAME/LATEGAME] 非空
            # (玩家点 PWA toast confirm 切 doctrine 走 strategy_set directive → fill slot)。
            # 注:default opening(4bg/12pool 等)启动时**不**走 strategy_set,所以
            # 不能依赖 board.slots[OPENING] 是否非空(default opening 永远 None)。
            persistent_set = (
                self.board.slots.get(StageKind.MIDGAME) is not None
                or self.board.slots.get(StageKind.LATEGAME) is not None
            )
            if not persistent_set:
                self.facade.set_sustain_uncap_active(True)
                self._sustain_uncap_triggered = True
                _via = (
                    "opening_completed" if _normal_ready else f"fallback@{_SUSTAIN_FALLBACK_S:.0f}s"
                )
                logger.warning("opening_sustain_uncap_triggered: now=%.1fs via=%s", now, _via)

        # Task #350: persistent_doctrine build_acceptance 用。
        # opening_completed + _auto_switch_delay_s 秒后自动 facade.set_build(target)。
        # 模拟玩家 PWA toast confirm 切 doctrine,让 persistent plan 跑起来。
        # _auto_switch_to 空串时整个 if 不成立,生产 / 普通 opening 验收完全不受影响。
        if (
            self._auto_switch_to
            and not self._auto_switch_triggered
            and self._opening_completed_signaled
            and self._opening_completed_at is not None
            and now - self._opening_completed_at > self._auto_switch_delay_s
        ):
            self.facade.set_build(self._auto_switch_to)
            self._auto_switch_triggered = True
            logger.warning(
                "auto_switch_to triggered: %s (build_acceptance test, %.1fs after opening_completed)",
                self._auto_switch_to,
                now - self._opening_completed_at,
            )

        # 不再自动 submit transition directive;只更新 self._pending_recommendation,
        # 等玩家 confirm_recommendation 才真正 submit(见 game_process 上行帧)。
        prev_reco = self._pending_recommendation
        self._update_recommendation(now)
        # 推荐变化时也推一次 snapshot(否则用户要等下次兜底周期)
        if prev_reco != self._pending_recommendation:
            need_snapshot = True

        # 兜底周期推（P0-2）
        self._tick_count += 1
        if self._tick_count >= self.config.snapshot_interval_ticks:
            self._tick_count = 0
            need_snapshot = True

        if need_snapshot:
            self._push_snapshot(now)

        # WP-A Task 7: 每 tick 更新受控单位画框清单（debug draw）
        try:
            self._push_debug_marks()
        except Exception as exc:
            logger.debug("_push_debug_marks fail: %s", exc)

        # WP-E: bot 关键动作自评（丢分矿 / 大波损兵）
        try:
            self._maybe_self_eval(now)
        except Exception as exc:
            logger.debug("_maybe_self_eval fail: %s", exc)

        return events

    # ------------------------------------------------------------------
    # 2026-05-24 用户:STANDBY 待命指令 每 tick 控位
    # ------------------------------------------------------------------

    # 待命半径(单位距 standby pos 超此距离即拉回)
    _STANDBY_RADIUS: float = 10.0
    # 受敌检测半径(在此范围内有敌方 → 自动 attack-move 最近敌方)
    _STANDBY_ENGAGE_RADIUS: float = 12.0

    @staticmethod
    def _already_heading_to(u: Any, pos: Any, tol: float = 1.8) -> bool:
        """单位是否已在朝 pos 移动(order_target 是 ~pos 的点)。

        用于避免每帧重发 move —— 慢速大单位(航母)每帧重发 move 会打断加速/寻路而抽搐
        (用户 2026-06-17「目标坐标锁定」规则)。idle / 朝别处走 → False(该下令);朝 pos 走 → True。
        """
        from sc2.position import Point2

        try:
            ot = u.order_target  # Point2(移动目标) / int(攻击目标 tag) / None
            if isinstance(ot, Point2):
                return bool(ot.distance_to(pos) <= tol)
        except Exception:
            return False
        return False

    @staticmethod
    def _order_target_tag(u: Any) -> int | None:
        """单位当前 order 的目标 tag(攻击/跟随某单位时);移动到点/无序 → None。"""
        try:
            ot = u.order_target
            return int(ot) if isinstance(ot, int) else None
        except Exception:
            return None

    def _tick_standing_order_deaths(self, now: float) -> None:
        """#3 用户:持久指令(L3 standing order)认领的单位全部阵亡 → 自动终止。

        镜像 B 类 squad 的全死处理(_tick_squad_backfill 的 ③):
        - 逐个 standing order 剪掉 _standing_order_tags 里已死的 tag。
        - 曾认领过单位(tags 非空)且现在全死 → _release_directive_done(units_lost)。
          → _override_status 设 done/units_lost,L3 卡转暗红"单位全失",grace 后消失。
        - 从未认领到单位(tags 空)不算"单位死光",跳过。

        持久指令不做 backfill(不会自动抓新单位顶替)—— 单位打光即任务终结,符合
        玩家"这队没了"的预期。要继续就重新下指令。
        """
        if not self.standing_orders or self._bot is None:
            return
        try:
            alive = {u.tag for u in self._bot.units}
        except Exception:
            return
        for d in list(self.standing_orders):
            if d.id in self._done_at:
                continue  # 已在 done grace 期,别重复释放
            tags = self._standing_order_tags.get(d.id)
            if not tags:
                continue  # 从未认领到单位 — 不算"单位死光"
            live = tags & alive
            for dead in tags - alive:
                self._unit_semantics.pop(dead, None)  # 死单位清语意标签
            if live:
                self._standing_order_tags[d.id] = live  # 剪掉死的,留活的
                continue
            # 认领过单位且现在全死 → units_lost(同 squad 暗红"单位全失"语义)
            logger.info("standing_order %s 单位全失,auto_terminated", d.id[:8])
            self._release_directive_done(d, now, "units_lost")

    def _tick_standby_orders(self) -> None:
        """所有 verb=STANDBY 的 standing order 每 tick 控位逻辑。

        每个 standby directive 的 selector units:
        - 距 standby_pos > _STANDBY_RADIUS → move(standby_pos)(拉回)
        - 范围内有敌方 → attack(最近敌方)(自动战斗)
        - 否则 hold(不发新命令,Reserved 单位不会被 sharpy 派别的)
        """
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.task import Verb

        bot = getattr(self, "_bot", None)
        if bot is None:
            return
        try:
            from sc2.position import Point2
        except Exception:
            return

        # 取 named_spot resolver 拿 standby 坐标(对齐 build_at_named_spot 路径)
        try:
            from vibecraft.bot.named_spot import NamedSpotRegistry

            registry = NamedSpotRegistry()
        except Exception:
            return

        # 2026-06-06 问题(代理建造链拉扯):正在代理建造的农民 tag,standby 必须放手——
        # 否则农民走去建下一个建筑(水晶/VS/BG)时离 standby 点远了,standby 每帧把它
        # move 回去 → 打断 build(BG 没下下去、VS#2 中断)+ 触发 _tick_proxy_build 重发
        # → 拍出两个水晶。build 优先,建完该卡从 _pending_proxy_build 移除,standby 才接管。
        busy_build_tags: set[int] = set()
        for _info in self._pending_proxy_build.values():
            with contextlib.suppress(Exception):
                busy_build_tags.add(int(_info["tag"]))
        # 2026-06-08 修 P3(代理建造 standby 农民拉扯):水晶 settle→VS 激活的**间隙**,农民不在
        # _pending_proxy_build 但仍属"进行中的链"(还有等 chain_structure_ready 的后续卡)。
        # 这段别让 standby 把它拉回 standby 点。**只在 gap 阶段(链已有建好的建筑)才放手** ——
        # 否则初次还没把农民送到工地(_chain_structures 还空)就放手 → standby 不送 → _pick 选别的
        # 农民建 → 链断(真局自验抓到的回归)。
        with contextlib.suppress(Exception):
            _active_chains = {
                getattr(getattr(d.payload, "activate_when", None), "chain_id", None)
                for d in self._pending_activation.values()
                if getattr(getattr(d.payload, "activate_when", None), "kind", None)
                == "chain_structure_ready"
            }
            for _cid in _active_chains:
                if self._chain_structures.get(_cid):  # 仅 gap(已有建好的建筑)才放手
                    busy_build_tags |= self._task_chains.get(_cid, set())

        for d in self.standing_orders:
            payload = d.payload
            if not isinstance(payload, UnitClaimPayload):
                continue
            if payload.task.primary_action.verb != Verb.STANDBY:
                continue
            tags = self._standing_order_tags.get(d.id, set())
            if not tags:
                continue
            # resolve 待命坐标
            target = payload.task.primary_action.target
            pos = None
            try:
                if target.named_spot:
                    pos = registry.resolve(target.named_spot, bot)
                elif target.point:
                    pos = Point2(target.point)
            except Exception:
                continue
            if pos is None:
                continue

            # 取实时单位
            try:
                units = bot.units.tags_in(tags)
            except Exception:
                continue

            for u in units:
                # 正在代理建造(含进行中的链)→ 放手,让 build 跑完(不 move 回 standby,不打断)
                if int(getattr(u, "tag", -1)) in busy_build_tags:
                    continue
                try:
                    d_pos = u.distance_to(pos)
                except Exception:
                    continue
                # 1. **还在回来的路上(超半径)→ 只管走回 standby 点,不被沿途敌人勾住**。
                #    2026-06-08 修 P10(航母回家抽搐):受敌 attack 之前先判距离 —— 否则慢速单位
                #    (航母)回家途中每帧被"范围内有敌→attack 最近敌"勾住、每帧换目标 → 抽搐、到不了家。
                #    回家/撤退途中就该一路走(同规则4:撤退用 move 不接敌),到点了再守。
                if d_pos > self._STANDBY_RADIUS:
                    # 2026-06-17 修(用户规则:目标锁定,别每帧重发):**已经在往 pos 走就不重发** move。
                    # 慢速大单位(航母)每帧重发 move 会打断加速/寻路 → 原地抽搐到不了家(P10 改成
                    # 每帧 move 后残留的抖)。仅当还没朝 pos 走(idle / 目标不是 pos)时才下令。
                    _heading = self._already_heading_to(u, pos)
                    # A/B 开关(仅自验):NO_DEDUPE → 退回旧的每帧重发(对照组,验抽搐)。
                    _force = bool(os.environ.get("VIBECRAFT_STANDBY_NO_DEDUPE"))
                    _issued = _force or not _heading
                    if _issued:
                        with contextlib.suppress(Exception):
                            u.move(pos)
                    if os.environ.get("VIBECRAFT_STANDBY_TRACE"):
                        _role = None
                        with contextlib.suppress(Exception):
                            _role = self._bot.knowledge.roles.unit_role(u)
                        logger.info(
                            "STANDBYTRACE tag=%s d_pos=%.1f pos=(%.1f,%.1f) heading=%s ot=%s role=%s act=%s",
                            int(getattr(u, "tag", -1)),
                            d_pos,
                            pos.x,
                            pos.y,
                            _heading,
                            getattr(u, "order_target", None),
                            _role,
                            "MOVE_ISSUED" if _issued else "MOVE_SKIP",
                        )
                    continue
                # 2. 已到待命点 → 守位:范围内有敌就打(**农民除外** —— 农民追敌会跑远再回来=晃,
                #    2026-06-06 问题2/4;军队到点才 engage)。
                _is_worker = getattr(getattr(u, "type_id", None), "name", "") in _NON_ARMY_TYPES
                if not _is_worker:
                    try:
                        enemies_near = bot.enemy_units.closer_than(self._STANDBY_ENGAGE_RADIUS, u)
                        if enemies_near:
                            target_enemy = enemies_near.closest_to(u)
                            # 同理:已经在打这个敌就不重发 attack(别每帧刷)。
                            if self._order_target_tag(u) != int(getattr(target_enemy, "tag", -1)):
                                u.attack(target_enemy)
                    except Exception:
                        pass
                # 3. 到点 + 无敌 → hold(不发命令)

    # safe_move: 单位到达 target 阈值(距 < 5 grid)即视为 arrived
    _SAFE_MOVE_ARRIVED_DIST: float = 5.0

    def _tick_safe_move_orders(self, now: float) -> None:
        """safe_move directive 每 tick:用 plan_drop_path 算 waypoints,顺序 move。
        全员到达 target → mark directive done(走 _release_directive_done 5s grace)。
        """
        if not self._safe_move_tags:
            return
        bot = getattr(self, "_bot", None)
        if bot is None:
            return
        try:
            from sc2.position import Point2

            from vibecraft.bot.drop_path import plan_drop_path
            from vibecraft.bot.named_spot import NamedSpotRegistry
        except Exception:
            return
        registry = NamedSpotRegistry()

        done_ids: list[str] = []
        for did, entry in list(self._safe_move_tags.items()):
            # entry: (tags, target_dict[, engage]) —— engage 缺省 False(向后兼容旧 2 元组)
            tags, target_dict = entry[0], entry[1]
            engage = entry[2] if len(entry) > 2 else False
            # resolve target
            target_pos = None
            try:
                ns = target_dict.get("named_spot")
                pt = target_dict.get("point")
                if ns:
                    target_pos = registry.resolve(ns, bot)
                elif pt:
                    target_pos = Point2(pt)
            except Exception:
                continue
            if target_pos is None:
                continue

            # 实时单位
            try:
                units = bot.units.tags_in(tags)
            except Exception:
                continue
            if not units:
                # 全死 → directive 立刻 done
                done_ids.append(did)
                continue

            all_arrived = True
            for u in units:
                try:
                    d_pos = u.distance_to(target_pos)
                except Exception:
                    continue
                if d_pos < self._SAFE_MOVE_ARRIVED_DIST:
                    continue
                all_arrived = False
                # 用 plan_drop_path 拿 waypoints(每 tick 算 1 次,简化:从当前位置)
                try:
                    waypoints = plan_drop_path(u.position, target_pos, bot)
                except Exception:
                    waypoints = [u.position, target_pos]
                # 移动到第一个非起点 waypoint;engage=True 用 attack-move(沿途遇敌就打)
                if len(waypoints) >= 2:
                    next_wp = waypoints[1]
                    try:
                        if engage:
                            u.attack(next_wp)
                        else:
                            u.move(next_wp)
                    except Exception:
                        pass
            if all_arrived:
                done_ids.append(did)

        # 完成的 directive → _release_directive_done(走 5s grace)
        for did in done_ids:
            self._safe_move_tags.pop(did, None)
            d = self._in_flight.get(did)
            if d is not None:
                with contextlib.suppress(Exception):
                    self._release_directive_done(d, now, reason="safe_move_arrived")

    _PENDING_MOVE_TIMEOUT_S: float = 90.0

    def _tick_pending_move(self, now: float) -> None:
        """2026-05-27 Issue 3:pending move 每 tick re-resolve selector。

        玩家"出 X 然后让 X 去 Y" 复合指令:production_override 和 move 同帧 commit,
        X 还没生产出来,move 的 selector 解析返空 → 之前 safe_move 立刻 mark done。
        这里 hold 住直到 unit 出现,然后接管为 safe_move(safe=True)或直接派
        move(safe=False)。timeout 后放弃 release directive(unit 没造出来 / 被取消)。
        """
        if not self._pending_move:
            return
        if self._bot is None:
            return
        done_ids: list[str] = []
        for did, info in list(self._pending_move.items()):
            sel = info["selector"]
            # timeout 90s 还没 unit 就放弃
            if now - info["submitted_at"] > self._PENDING_MOVE_TIMEOUT_S:
                logger.warning(
                    "MOVE pending timeout: selector=%s 90s 内无 unit,放弃(id=%s)",
                    sel.unit_type,
                    did[:8],
                )
                done_ids.append(did)
                continue
            try:
                tags = self._resolve_selector_with_count(sel)
            except Exception as exc:
                logger.debug("pending_move resolve fail: %s", exc)
                continue
            if not tags:
                continue
            # unit 出现了,接管
            target_dict = info["target_dict"]
            safe = info["safe"]
            engage = info.get("engage", False)
            logger.warning(
                "MOVE pending resolved: selector=%s 找到 %d unit,派 move(id=%s)",
                sel.unit_type,
                len(tags),
                did[:8],
            )
            # vibecraft 2026-06-06 审计:晚解析到的 MOVE 单位也要脱离 bot 控制。submit 时
            # tags 为空 → _claim_directive_units 没认领到 → 这里补 set_unit_role + 登记 tag,
            # 否则新单位(尤其农民)被 sharpy 拉回 / 被全局战术抢走。
            self._standing_order_tags[did] = set(tags)
            for _t in tags:
                with contextlib.suppress(Exception):
                    self.facade.set_unit_role(_t, UnitRole.LLM_CONTROLLED)
            if safe:
                self._safe_move_tags[did] = (set(tags), target_dict, engage)
            else:
                move_verb = "attack_move" if engage else "move_to"
                for tag in tags:
                    self.facade.execute_unit_action(
                        unit_tag=tag, verb=move_verb, target=target_dict
                    )
                # 非 safe 一次性派完,directive done
                done_ids.append(did)
            self._pending_move.pop(did, None)
        for did in done_ids:
            self._pending_move.pop(did, None)
            d = self._in_flight.get(did)
            if d is not None:
                with contextlib.suppress(Exception):
                    self._release_directive_done(d, now, reason="pending_move_finalized")

    # 代理建造优先复用半径:建造点这么多格内已被持有的农民优先(= 链上同一农民)
    _PROXY_PREFER_RADIUS: float = 15.0

    def _pick_proxy_build_probe(self, point: tuple[float, float]) -> int | None:
        """选代理建造的农民。方案A:整条链用同一农民 —— 优先选"已被某指令持有
        (在 _standing_order_tags 里)且离建造点近"的农民(= card1 认领的/上一步那个);
        没有再退选离建造点最近的 worker。返回 tag,找不到 None。
        """
        bot = getattr(self, "_bot", None)
        if bot is None:
            return None
        try:
            from sc2.position import Point2

            p2 = Point2(point)
            workers = list(getattr(bot, "workers", None) or [])
            if not workers:
                return None
            owned: set[int] = set()
            for tags in self._standing_order_tags.values():
                owned |= tags
            held_near = [
                w
                for w in workers
                if int(w.tag) in owned and w.distance_to(p2) < self._PROXY_PREFER_RADIUS
            ]
            pool = held_near if held_near else workers
            closest = min(pool, key=lambda w: w.distance_to(p2))
            return int(closest.tag)
        except Exception as exc:
            logger.warning("_pick_proxy_build_probe fail: %s", exc)
            return None

    def _tick_proxy_build(self, now: float) -> None:
        """代理建造(build_at by_probe)tick:农民空闲且还没建好 → 重发 build,保证它真去建。
        **不自动放归** —— 建完继续待命,直到玩家点 × / "释放"(2026-06-06 用户)。
        只有农民死亡才关卡。
        """
        if not self._pending_proxy_build:
            return
        bot = getattr(self, "_bot", None)
        if bot is None:
            return
        try:
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2
        except Exception:
            return
        dead: list[str] = []  # 农民死了的卡 → 关
        settled: list[str] = []  # 已开始建造的卡 → 停止重发(但农民继续被持有待命)
        # 2026-06-06 真局自验发现:同一农民被两张 VS 卡共用、每帧各重发一次 build →
        # find_placement 每次返回不同点 → 农民每帧被改目标、永远走不到、建不出来(矿够也不建)。
        # 修:(a)序列化 —— 一个农民这帧只让一张卡发 build(VS#2 等 VS#1);(b)农民正忙(走去
        # 建/正在建)不重发;(c)空闲也节流(隔 _PROXY_REISSUE_THROTTLE_S 才重发),给它时间走到
        # 位开建,别每帧抖。
        issued_probe_tags: set[int] = set()
        for did, info in list(self._pending_proxy_build.items()):
            tag = int(info["tag"])
            pt = info["point"]
            st = str(info["structure"])
            try:
                u = bot.units.by_tag(tag)
            except Exception:
                u = None
            if u is None:
                dead.append(did)
                continue
            # 已开始建造(目标点 3 格内有该建筑,在建/完成都算)→ 停止重发;农民仍被持有,
            # 建完站那待命,玩家 × 才放归(不在此放归)。
            # 抓住"这一个"建筑的 tag,绑到农民所属的 chain → 后续步骤(gateway)用
            # activate_when=chain_structure_ready 精确等它建好,不靠全局计数/距离猜。
            #
            # ⚠️ 代理建造"完成判定"踩过两类 bug,改这块前先读全(都在本函数 + 下面收尾循环):
            #   (1) **误判 settle**(玩家:在某点造 VS,卡片秒变已完成、VS 没造):农民出发时途经
            #       家里旧 VS → closer_than(3.5,农民位置)命中旧建筑。修:settle 排除 info
            #       ["preexisting"](发起时快照的旧 tag)+ _proxy_claimed_structs(别卡认领的)。
            #   (2) **卡片不消失**(玩家:水晶/VS 建好卡还在):激活的 build_at 卡进
            #       _committed_directives(非 _in_flight),收尾 release 两处都要查(见下面 settled/dead
            #       循环 `_in_flight.get(did) or _committed_directives.get(did)`)。
            try:
                tid = getattr(UnitTypeId, st.upper(), None)
                if tid is not None:
                    # settle 检测:find_placement(696138d)可能把建筑放在离原始点几格外,
                    # 不能只看原始点 3 格 —— 否则检测不到 → 链永不绑定 → 后续 VS 的
                    # chain_structure_ready 永不触发(玩家报:VS 建到家里 + 农民干站 standby
                    # 被拉扯)。农民必站在自己工地旁,优先用农民位置找(最稳);农民走开了
                    # 再放宽原始点半径兜底。代理点偏远,同类型建筑不会撞,放宽安全。
                    # 排除已被别的卡认领的同类型建筑(同链两个 VS:第2张卡不能 settle 到
                    # 第1张卡刚建的那个,否则只建一个)。
                    # 排除:已被别的卡认领的(_proxy_claimed_structs)+ **本卡发起时就已存在的
                    # 同类建筑**(info["preexisting"])。后者防"农民途经家里旧 VS → 误判造好"。
                    _preexist = info.get("preexisting") or set()
                    structs = bot.structures(tid).filter(
                        lambda s, _pe=_preexist: (
                            int(s.tag) not in self._proxy_claimed_structs and int(s.tag) not in _pe
                        )
                    )
                    near = structs.closer_than(3.5, u.position)
                    if not near.exists:
                        near = structs.closer_than(8.0, Point2(pt))
                    if near.exists:
                        settled.append(did)
                        try:
                            anchor = (
                                u.position
                                if structs.closer_than(3.5, u.position).exists
                                else Point2(pt)
                            )
                            s_obj = min(near, key=lambda s: s.distance_to(anchor))
                            s_tag = int(s_obj.tag)
                            self._proxy_claimed_structs.add(s_tag)  # 认领,后续卡不再撞它
                            # 优先从 _pending_proxy_build[did]["chain_id"] 获取(execute 时存入,
                            # 不依赖 builder tag 在 _task_chains 里 ——
                            # 水晶卡可能用链外农民 build,tag 反查会失败)。
                            # 兼容旧路径:chain_id 未存时仍按 builder tag 反查。
                            cid = info.get("chain_id") or next(
                                (c for c, ts in self._task_chains.items() if tag in ts), None
                            )
                            if cid is not None:
                                self._chain_structures.setdefault(cid, set()).add(s_tag)
                                # **水晶建好那一刻,刷新本链所有 VS/后续建筑卡的坐标**为水晶周围
                                # 不同的点(每张一个方向)→ 各占一边,不再各自现找撞同一格
                                # (2026-06-07 用户)。Pylon(能量源)settle 时做。
                                if st.upper() == "PYLON":
                                    self._assign_chain_followup_spots(
                                        cid, (float(s_obj.position.x), float(s_obj.position.y))
                                    )
                            logger.info(
                                "PROXYTRACE settled did=%s type=%s s_tag=%d chain=%s spos=(%.1f,%.1f)",
                                did[:8],
                                st,
                                s_tag,
                                cid,
                                float(s_obj.position.x),
                                float(s_obj.position.y),
                            )
                        except Exception:
                            pass
                        continue
            except Exception:
                pass
            # 序列化:同一农民这帧已被另一张卡处理 → 本卡等下一轮(VS#2 等 VS#1 干完)
            if tag in issued_probe_tags:
                continue
            # **关键(forward_proxy 同款经验)**:不能用 is_idle 判"在不在建造"——SC2 默认
            # auto-mining 让空闲农民带 HARVEST_GATHER 订单、is_idle=False,但其实是被 sharpy
            # 拽着走回家挖矿。旧代码 `if not is_idle: skip` 把"正走回家挖矿的农民"当成"在建造"
            # 跳过、永不重新指挥 → 远程代理农民走回家,野外建筑建不出来(玩家:VS 都在家里)。
            # 改判 orders 里有没有 PROTOSSBUILD_* 建造订单:有=真在 warp 建筑,别打断;
            # 没有(空闲 / auto-mining 被拽走)→ 每帧重发 build,落点缓存稳定不抖,配合
            # super().on_step() 之后的 post-super drain → build 成为最后一道命令、压过 gather。
            try:
                orders_ids = [
                    str(getattr(getattr(o, "ability", None), "id", "")) for o in (u.orders or [])
                ]
            except Exception:
                orders_ids = []
            if any("BUILD" in o for o in orders_ids):
                issued_probe_tags.add(tag)  # 真在 warp 自己的建筑,别打断
                continue
            # 没在建 → 重发 build 把农民拉回野外工地(压过 auto-mining 的 gather)
            self.facade.order_probe_build(tag, st, pt, cache_key=did)
            issued_probe_tags.add(tag)
        for did in settled:
            self._pending_proxy_build.pop(did, None)
            with contextlib.suppress(Exception):
                self.facade._proxy_place_cache.pop(did, None)  # 清落点缓存
            # 卡片完成处理(玩家报:水晶/VS 建好了卡片还显示没完成):
            # - 本卡**不持有农民**(链式:农民由 card0 standby 持有)→ 建好即标"完成"消失;
            # - 本卡**自己持有农民**(单卡代理建造)→ 农民待命到玩家×,卡保留(状态已是 active)。
            # 注:build_at 卡从 _pending_activation 激活后进 _committed_directives(不是 _in_flight),
            # 所以两处都查,否则查不到 directive → 永不标完成(玩家报"卡不消失"根因)。
            if not self._standing_order_tags.get(did):
                d = self._in_flight.get(did) or self._committed_directives.get(did)
                if d is not None:
                    with contextlib.suppress(Exception):
                        self._release_directive_done(d, now, reason="proxy_built")
        for did in dead:
            self._pending_proxy_build.pop(did, None)
            with contextlib.suppress(Exception):
                self.facade._proxy_place_cache.pop(did, None)
            d = self._in_flight.get(did) or self._committed_directives.get(did)
            if d is not None:
                with contextlib.suppress(Exception):
                    self._release_directive_done(d, now, reason="units_lost")

    # 水晶旁后续建筑的预分配落点方向(地图寻路信息不可用时的兜底固定偏移)。
    _CHAIN_SPOT_OFFSETS: tuple[tuple[float, float], ...] = (
        (4.0, 0.0),
        (-4.0, 0.0),
        (0.0, 4.0),
        (0.0, -4.0),
        (3.0, 3.0),
        (-3.0, -3.0),
    )

    def _assign_chain_followup_spots(self, cid: str, pylon_pos: tuple[float, float]) -> None:
        """水晶(Pylon)建好那一刻,把本链所有"还在等"的后续 by_probe 建筑卡(两个 VS 等)的
        落点坐标**提前规划**好 —— 用地图可寻路信息挑"周围最空旷、互相分开"的点,从布局上就
        **不把农民围死**(2026-06-07 用户:不能放在矿后/贴崖被矿+建筑夹死的点,要从地图位置算)。

        写进 payload.point(并清 named_spot)→ 卡激活时直接用这个点。
        覆盖 _pending_activation(还在等水晶 ready)里本链的 by_probe build_at。
        """
        from vibecraft.directives.models import BuildAtPayload

        followups = [
            d
            for d in self._pending_activation.values()
            if isinstance(getattr(d, "payload", None), BuildAtPayload)
            and d.payload.by_probe  # type: ignore[union-attr]
            and getattr(getattr(d.payload, "activate_when", None), "kind", None)
            == "chain_structure_ready"
            and getattr(d.payload.activate_when, "chain_id", None) == cid
        ]
        if not followups:
            return
        spots = self._pick_open_cluster_spots(pylon_pos, len(followups))
        for d, pt in zip(followups, spots, strict=False):
            d.payload.point = pt  # type: ignore[union-attr]
            d.payload.named_spot = None  # type: ignore[union-attr]
            with contextlib.suppress(Exception):
                logger.info(
                    "PROXYTRACE assign_spot did=%s type=%s point=(%.1f,%.1f)",
                    d.id[:8],
                    d.payload.structure_type,
                    pt[0],
                    pt[1],  # type: ignore[union-attr]
                )

    def _pick_open_cluster_spots(
        self, pylon_pos: tuple[float, float], n: int
    ) -> list[tuple[float, float]]:
        """在水晶周围(能量场内)挑 n 个落点,让"水晶 + 这些建筑 + 地图障碍(矿/崖)"**不围死**
        农民。每个候选点按"周围 8 方向 3 格处有多少是可寻路空地(矿/崖/已选建筑都算堵)"打分,
        贪心选最空旷、且互相 ≥3.5 格分开的点 → 农民始终有多个方向能走出去(不被夹死)。
        地图信息不可用 → 退回固定偏移。
        """
        import math

        bot = getattr(self, "_bot", None)
        px, py = float(pylon_pos[0]), float(pylon_pos[1])

        def _pathable(qx: float, qy: float) -> bool:
            if bot is None:
                return True
            try:
                from sc2.position import Point2

                return bool(bot.in_pathing_grid(Point2((qx, qy))))
            except Exception:
                return True

        blocked: list[tuple[float, float]] = [(px, py)]  # 水晶占地 + 后续选中的点

        def _openness(cx: float, cy: float) -> int:
            cnt = 0
            for k in range(8):
                a = k * math.pi / 4.0
                qx, qy = cx + 3.0 * math.cos(a), cy + 3.0 * math.sin(a)
                if not _pathable(qx, qy):
                    continue
                if any((qx - bx) ** 2 + (qy - by) ** 2 < 2.5**2 for bx, by in blocked):
                    continue
                cnt += 1
            return cnt

        # 候选:能量场内多半径多角度,**只留可走的**(in_pathing_grid;矿后/崖上直接排除)
        cand_pts: list[tuple[float, float]] = []
        for r in (4.0, 4.5, 5.0, 5.5, 3.5):
            for k in range(16):
                a = k * math.pi / 8.0
                pt = (px + r * math.cos(a), py + r * math.sin(a))
                if _pathable(pt[0], pt[1]):
                    cand_pts.append(pt)

        # **最远点采样**:第 1 个选最空旷;之后每个选"离已选最远"的可走点(≥3.5)。
        # → 两侧都开:自然对开、相距 ~2r 不重合;只一侧开:在该侧尽量拉开、仍不塞崖。
        # 解决 2026-06-07"两 VS 重合"(旧 openness 贪心把两个都挑到同一侧只隔 ~5 格 →
        # find_placement 各自往水晶拽 → 撞一起、第 2 个建不出)。
        chosen: list[tuple[float, float]] = []
        if cand_pts:
            seed = max(cand_pts, key=lambda p: _openness(p[0], p[1]))
            chosen.append(seed)
            blocked.append(seed)
        while len(chosen) < n and cand_pts:
            best: tuple[float, float] | None = None
            best_key: tuple[float, int] = (-1.0, -1)
            for pt in cand_pts:
                mind = min(((pt[0] - c[0]) ** 2 + (pt[1] - c[1]) ** 2) ** 0.5 for c in chosen)
                if mind < 3.5:  # 离已选太近 → 会撞,跳过
                    continue
                key = (mind, _openness(pt[0], pt[1]))  # 先比最远,再比空旷
                if key > best_key:
                    best_key, best = key, pt
            if best is None:
                break
            chosen.append(best)
            blocked.append(best)

        # 不够 n → 固定偏移兜底补齐(地图信息缺失/全太挤时)
        i = 0
        while len(chosen) < n:
            ox, oy = self._CHAIN_SPOT_OFFSETS[i % len(self._CHAIN_SPOT_OFFSETS)]
            cand = (px + ox, py + oy)
            if cand not in chosen:
                chosen.append(cand)
            i += 1
            if i > 20:
                break
        return chosen

    # 折跃门兵种(能折跃出来的)。机械/空军/不朽等没法折跃 → "在X刷"忽略 warp_at 走正常出兵。
    _WARP_CAPABLE_UNITS: frozenset[str] = frozenset(
        {"zealot", "stalker", "adept", "sentry", "hightemplar", "darktemplar"}
    )

    def _is_warp_capable(self, unit_type: str) -> bool:
        return str(unit_type).lower() in self._WARP_CAPABLE_UNITS

    def pending_build_reservations(self) -> list[str]:
        """玩家代理建造(by_probe)未完成的建筑 type 名列表 → 让 bot 自主 macro 给这些钱让路。

        问题3(玩家指令优先花钱权,2026-06-06 用户:"我有指令的时候,家里那个要暂停")。
        代理建造农民走 facade.order_probe_build → u.build() **直接花原始矿**(ai.minerals),
        绕过 sharpy knowledge.can_afford 的 reserved 扣减;而 bot 自主 macro 都走 can_afford。
        两者抢同一笔钱、谁先轮到谁花 → bot 先在家里出同类建筑,代理建造就没钱。
        bot 侧每帧(knowledge 刚清零 reserved、ActManager 跑 plan 花钱之前)把本列表里的
        建筑 cost 登记进 reserved → 自主 macro can_afford 看到的钱变少 → 让路攒矿 →
        代理农民(花原始矿,不受 reserved 影响)拿到这笔让出来的钱。

        覆盖两种待建状态:
        1. 正在代理建造(农民已派、还没 settled)—— `_pending_proxy_build`。
        2. 链式 by_probe build_at 还挂在 `_pending_activation` 等前一步(如"等水晶好了修 VS")
           —— 在"等"的时候就开始锁钱,否则等待期间 macro 把钱花掉。
        只锁 by_probe(花钱绕过 reserved);structure_override 那类走 macro 自己花,锁了会死锁。
        """
        from vibecraft.directives.models import BuildAtPayload

        out: list[str] = []
        for info in self._pending_proxy_build.values():
            st = info.get("structure")
            if st:
                out.append(str(st))
        for d in self._pending_activation.values():
            payload = getattr(d, "payload", None)
            if isinstance(payload, BuildAtPayload) and payload.by_probe and payload.structure_type:
                out.append(str(payload.structure_type))
        return out

    def _resolve_target_spec_point(self, ts: Any) -> tuple[float, float] | None:
        """TargetSpec → (x, y) 坐标。

        Task F 巡逻两点解析用。
        - kind=POINT / CAMERA: 直接取 ts.point
        - kind=NAMED_SPOT: 走 NamedSpotRegistry 解析（需要 _bot）
        - 其他 kind / 解析失败 → None
        """
        from vibecraft.directives.scope import TargetKind

        kind = getattr(ts, "kind", None)
        if kind in (TargetKind.POINT, TargetKind.CAMERA):
            pt = getattr(ts, "point", None)
            if pt is not None:
                return (float(pt[0]), float(pt[1]))
            return None
        if kind == TargetKind.NAMED_SPOT:
            ns = getattr(ts, "named_spot", None)
            if ns is None:
                return None
            bot = self._bot
            if bot is None:
                return None
            try:
                from vibecraft.bot.named_spot import NamedSpotRegistry

                reg = NamedSpotRegistry()
                resolved = reg.resolve(ns, bot)
                if resolved is not None:
                    return (float(resolved.x), float(resolved.y))
            except Exception as exc:
                logger.debug("patrol named_spot resolve fail: %s", exc)
            return None
        return None

    def _tick_patrol(self, now: float) -> None:
        """Task F 巡逻驱动：每帧判到位后切换目标点，A→B→A→B 无限往返。"""
        if not self._pending_patrol:
            return
        ARRIVE = 4.0
        for did, info in list(self._pending_patrol.items()):
            tag: int = info["tag"]
            points: list[tuple[float, float]] = info["points"]
            idx: int = info["idx"]
            pos = self.facade.get_unit_position(tag)
            if pos is None:
                # 单位死了或不存在 → 清除
                self._pending_patrol.pop(did, None)
                continue
            cur = points[idx]
            dist = ((pos[0] - cur[0]) ** 2 + (pos[1] - cur[1]) ** 2) ** 0.5
            if dist <= ARRIVE:
                idx = 1 - idx  # 切到另一点 (0↔1)
                info["idx"] = idx
                next_pt = points[idx]
                self.facade.execute_unit_action(
                    unit_tag=tag,
                    verb="move_to",
                    target={"kind": "point", "point": list(next_pt)},
                )

    def _tick_recruit_watchers(self, now: float) -> None:
        """2026-06-13 持续征兵：每 tick 把新出现的匹配单位并入编队或 standing order。

        watcher 结构：{kind: "group"|"claim", group_id|None, unit_type: str, seen: set[int]}
        懒清理：对应 directive 不在 _in_flight（group）或 standing_orders（claim）时自动 pop。
        """
        if not self._recruit_watchers:
            return
        standing_ids = {d.id for d in self.standing_orders}
        for did in list(self._recruit_watchers.keys()):
            watcher = self._recruit_watchers.get(did)
            if watcher is None:
                continue
            kind = watcher["kind"]
            # 懒清理：directive 已不活跃则删 watcher
            if kind == "group" and did not in self._in_flight:
                self._recruit_watchers.pop(did, None)
                continue
            if kind == "claim" and did not in standing_ids:
                self._recruit_watchers.pop(did, None)
                continue
            ut = watcher["unit_type"]
            if not ut:
                continue
            # target_count=0 短路（opus D3）：claim 暂停时跳过 resolve_selector，别每帧空扫全图
            if kind == "claim":
                _dir_peek = next((d for d in self.standing_orders if d.id == did), None)
                if _dir_peek is not None:
                    from vibecraft.directives.models import UnitClaimPayload as _UCP_PEEK

                    _pl_peek = _dir_peek.payload
                    if isinstance(_pl_peek, _UCP_PEEK) and _pl_peek.target_count == 0:
                        continue
            current: set[int] = set(self.facade.resolve_selector(unit_type=ut))
            new_tags = current - watcher["seen"]
            if not new_tags:
                continue
            watcher["seen"] |= new_tags
            if kind == "group":
                gid = watcher["group_id"]
                if gid is not None:
                    self._voice_groups.setdefault(gid, set())
                    self._voice_groups[gid] |= new_tags
                    logger.info(
                        "RECRUIT group=%d +%d tags=%s",
                        gid,
                        len(new_tags),
                        sorted(new_tags),
                    )
            elif kind == "claim":
                existing = self._standing_order_tags.setdefault(did, set())
                # 查对应 directive 拿 action + target_count cap
                directive = next((d for d in self.standing_orders if d.id == did), None)
                if directive is None:
                    continue
                from vibecraft.directives.models import UnitClaimPayload as _UCP

                payload = directive.payload
                if not isinstance(payload, _UCP):
                    continue
                # target_count cap（opus D1）：入伍前判 len(当前群) < target_count
                tc = payload.target_count
                if tc is not None:
                    available_slots = max(0, tc - len(existing))
                    if available_slots == 0:
                        continue  # 已满，跳过本次入伍
                    new_tags = set(list(new_tags)[:available_slots])
                existing |= new_tags
                action = payload.task.primary_action
                verb_str = action.verb.value
                target_dump = action.target.model_dump(mode="json") if action.target else None
                # group_harass / harass_workers：director 只维护 tag 集，微操由 director tick 主动调度
                skip_recruit_action = verb_str in ("group_harass", "harass_workers")
                for tag in new_tags:
                    # 新出的单位通常无主，但仍调 _supersede_conflicting_moves 保险
                    with contextlib.suppress(Exception):
                        self._supersede_conflicting_moves(
                            {tag},
                            keep_id=did,
                            now=now,
                        )
                    with contextlib.suppress(Exception):
                        self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
                    if not skip_recruit_action:
                        with contextlib.suppress(Exception):
                            self.facade.execute_unit_action(
                                unit_tag=tag,
                                verb=verb_str,
                                target=target_dump,
                                ability_id=action.ability_id,
                            )
                logger.info(
                    "RECRUIT claim id=%s +%d tags=%s",
                    did[:8],
                    len(new_tags),
                    sorted(new_tags),
                )

    @staticmethod
    def _compare_op(lhs: float, op: str, rhs: float) -> bool:
        if op == ">=":
            return lhs >= rhs
        if op == ">":
            return lhs > rhs
        if op == "<=":
            return lhs <= rhs
        if op == "<":
            return lhs < rhs
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        return False

    def _is_activation_satisfied(self, activate_when: Any, directive: Any = None) -> bool:
        """检查 activate_when 条件是否已满足。

        directive(2026-06-06):提供时,unit_arrived 用**该指令的单位重心**判到达(支持
        群组/链命令);不提供则 fallback 任意农民(代理建造旧路径)。

        独立 check(不依赖 task_monitor 的 event 订阅,因为 activate_when 不走
        attach_directive 路径)。覆盖常用 activation kinds:
          - tech_done: bot.state.upgrades
          - structure_count: bot.structures(type).ready.amount
          - expansion_count: bot.townhalls.amount
          - all_of / any_of: 递归

        其他 kind(unit_count_built_since 等 delta 语义)无 baseline,activation
        无意义,返 True(立刻激活)避免卡死。
        """
        if activate_when is None:
            return True
        if self._bot is None:
            return False
        # 支持 pydantic model 和 dict 两种形式
        if hasattr(activate_when, "model_dump"):
            dw = activate_when.model_dump(mode="json")
        elif isinstance(activate_when, dict):
            dw = activate_when
        else:
            return True
        kind = dw.get("kind", "")
        op_str = dw.get("op", ">=")
        try:
            if kind == "tech_done":
                upgrade_id = dw.get("upgrade_id", "")
                if not upgrade_id:
                    return False
                from sc2.ids.upgrade_id import UpgradeId

                # case-insensitive match against UpgradeId enum
                u_name = upgrade_id.upper()
                try:
                    u_enum = UpgradeId[u_name]
                except KeyError:
                    return False
                return u_enum in self._bot.state.upgrades
            if kind in ("structure_count", "structure_count_built_since"):
                # structure_count_built_since 本是 done_when 的增量语义;作 activate 门时
                # 没 baseline → 退化成"当前 ready 数"判定,避免落到未知分支卡死。
                # 注意:这是**全局**计数(家里的 pylon 也算)。要"目标点那一个建好"请用
                # structure_ready_near(只数目标点附近的)。
                stype = dw.get("structure_type", "")
                value = int(dw.get("value", 0))
                from sc2.ids.unit_typeid import UnitTypeId

                try:
                    t_id = UnitTypeId[stype.upper()]
                except KeyError:
                    return False
                count = int(self._bot.structures(t_id).ready.amount)
                return self._compare_op(count, op_str, value)
            if kind in ("own_unit_count", "unit_count_built_since"):
                # 同理:作 activate 门时退化成"当前该兵种数"判定,避免落未知分支卡死。
                utype = dw.get("unit_type", "")
                value = int(dw.get("value", 0))
                from sc2.ids.unit_typeid import UnitTypeId

                try:
                    u_id = UnitTypeId[utype.upper()]
                except KeyError:
                    return False
                count = int(self._bot.units(u_id).amount)
                return self._compare_op(count, op_str, value)
            if kind == "structure_ready_near":
                # 2026-06-06 用户:gateway 必须在 pylon 能量场内 → 等"目标点附近那一个
                # 建好的 pylon"(而非全局 pylon>=1,否则家里有 pylon 就立即放行)。
                stype = dw.get("structure_type", "")
                area = dw.get("area")
                within = float(dw.get("within_grid", 8.0))
                from sc2.ids.unit_typeid import UnitTypeId
                from sc2.position import Point2

                try:
                    t_id = UnitTypeId[stype.upper()]
                except KeyError:
                    return False
                point = self._resolve_target_area(area)
                if point is None:
                    return False
                if not isinstance(point, Point2):
                    point = Point2((float(point[0]), float(point[1])))
                try:
                    return bool(self._bot.structures(t_id).ready.closer_than(within, point).exists)
                except Exception:
                    return False
            if kind == "chain_structure_ready":
                # 2026-06-06 用户(推荐方式):连续指令里"前一步农民造的那一个建筑"建好。
                # 农民 build 出建筑瞬间 _tick_proxy_build 抓住它的 tag 绑到 chain;这里按
                # **tag** 精确判它是否已 ready —— 不靠全局计数、不靠距离猜,不会误判。
                cid = dw.get("chain_id")
                s_tags = self._chain_structures.get(cid, set()) if cid else set()
                if not s_tags:
                    return False  # 链上还没记下建好的建筑(农民还没把它造出来)
                try:
                    ready_tags = {int(s.tag) for s in self._bot.structures.ready}
                except Exception:
                    return False
                return bool(s_tags & ready_tags)
            if kind == "expansion_count":
                value = int(dw.get("value", 0))
                count = len(self._bot.townhalls)
                return self._compare_op(count, op_str, value)
            if kind == "all_of":
                conds = dw.get("conditions", [])
                return all(self._is_activation_satisfied(c, directive) for c in conds)
            if kind == "any_of":
                conds = dw.get("conditions", [])
                return any(self._is_activation_satisfied(c, directive) for c in conds)
            if kind == "unit_arrived":
                # 代理建造 activate_when：有己方农民到达目标点附近 → 满足。
                # area 接受 named_spot 字符串（走 _resolve_target_area）或
                # "(x, y)" 坐标字符串（_resolve_target_area 不解析此格式，自行 parse）。
                area = dw.get("area")
                # 2026-06-06 问题2修复:激活半径 ≥ standby 停靠半径。否则农民 standby 到
                # 距锚点 ~10 就停下(standby 满足),永远进不了默认 5 格 → 卡2 永不激活、
                # 水晶不修、农民干卡 standby。floor 到 standby 半径+2,到待命点即算到达。
                within = max(float(dw.get("within_grid", 5.0)), self._STANDBY_RADIUS + 2.0)
                point = None
                # 先尝试 "(x, y)" 格式坐标字符串
                if isinstance(area, str):
                    _s = area.strip().lstrip("(").rstrip(")")
                    _parts = [p.strip() for p in _s.split(",")]
                    if len(_parts) == 2:
                        try:
                            from sc2.position import Point2

                            point = Point2((float(_parts[0]), float(_parts[1])))
                        except Exception:
                            pass
                # 若坐标 parse 失败，走 _resolve_target_area（named_spot / tuple）
                if point is None:
                    point = self._resolve_target_area(area)
                if point is None:
                    return False
                try:
                    from sc2.position import Point2

                    if not isinstance(point, Point2):
                        point = Point2((float(point[0]), float(point[1])))
                    # 2026-06-06 问题5a:优先用"本指令的单位"重心判到达(群组/链)。之前只查
                    # self._bot.workers(农民)→ "一队虚空到X"这种群组命令永远不满足、卡死。
                    # 有 directive 上下文 → 解析它的 selector(group_id/chain/unit_type)单位,
                    # 判**重心**进圈;拿不到单位再 fallback 任意农民(代理建造无 directive 时)。
                    if directive is not None:
                        sel = getattr(getattr(directive, "payload", None), "selector", None)
                        if sel is not None:
                            try:
                                tags = set(self._resolve_selector_with_count(sel))
                            except Exception:
                                tags = set()
                            if tags:
                                units = self._bot.units.tags_in(tags)
                                if units:
                                    return bool(units.center.distance_to(point) <= within)
                                return False  # 指令单位全死 → 不算到达
                    return any(w.distance_to(point) <= within for w in self._bot.workers)
                except Exception:
                    return False
            # 2026-06-06 用户:未知 kind **默认不激活**(原来默认立即激活 → 任何没实现的
            # 激活门被当场放行,gateway 没等 pylon 就修。宁可不激活也别误放行)。
            logger.warning(
                "activate_when kind=%s 不支持 → 默认不激活(请改用已支持的 kind)",
                kind,
            )
            return False
        except Exception as exc:
            logger.debug("activate_when eval fail: %s", exc)
            return False

    def _tick_pending_activation(self, now: float) -> None:
        """每 tick 检查 _pending_activation directive 是否满足 activate_when 条件。"""
        if not self._pending_activation:
            return
        ready_ids: list[str] = []
        for did, d in list(self._pending_activation.items()):
            activate_when = getattr(d.payload, "activate_when", None)
            if self._is_activation_satisfied(activate_when, d):
                ready_ids.append(did)
        for did in ready_ids:
            d = self._pending_activation.pop(did)
            logger.warning(
                "directive activation satisfied: id=%s,calling _apply_to_facade now",
                did[:8],
            )
            # 走 _committed_directives 注册路径(同 _dispatch_committed_to_facade 后半段)
            from vibecraft.directives.models import (
                TacticalObjectivePayload,
                UnitClaimPayload,
            )

            is_persistent_unit_claim = (
                isinstance(d.payload, UnitClaimPayload) and d.payload.persistent
            )
            is_persistent_l2 = (
                isinstance(d.payload, TacticalObjectivePayload) and d.payload.persistent
            )
            if is_persistent_unit_claim:
                # persistent claim 走 standing 路径(注册 _standing_order_tags + standby tick +
                # ×-release),不是 _apply_to_facade 的 ephemeral 一次性路径。2026-06-06。
                self._set_override_status(d, "active", "")
                self._assign_standing_order_units(d)
                continue
            if (
                d.type
                not in (
                    DirectiveType.PRODUCTION_OVERRIDE,
                    DirectiveType.TECH_OVERRIDE,
                    DirectiveType.EXPANSION_OVERRIDE,
                    DirectiveType.STRUCTURE_OVERRIDE,
                    DirectiveType.DROP_ACT,
                    DirectiveType.STRATEGY_SET,
                    DirectiveType.STRATEGY_CANCEL,
                )
                and not is_persistent_unit_claim
                and not is_persistent_l2
            ):
                self._committed_directives[d.id] = d
            # 卡片激活后状态置"active"(执行中)—— 否则 UI 一直显示"未激活",即使已在建造/已建好
            # (玩家报:水晶/VS 都建出来了,卡片还显示"未激活")。
            self._set_override_status(d, "active", "")
            self._apply_to_facade(d, now)

    def _update_recommendation(self, now: float) -> None:
        """计算 self._pending_recommendation(opening 完成 → 推荐 midgame)。

        判定:opening 已设 + 当前 phase = last phase + midgame 空 → 推荐
        来源优先级:abort_signals 命中 > default_transitions[0] > LLM 兜底(留 TODO)

        不再自动 submit;玩家 confirm 后才走 submit_directives。
        如果当前推荐被玩家"忽略"(暂时没实现忽略状态,clear 由玩家点其它剧本或 voice 切覆盖)
        """
        if self.library is None:
            self._pending_recommendation = None
            return

        # 当前 stage 已有 slot 时不推荐(玩家已经决策)
        # opening → midgame
        if self.board.slots.get(StageKind.MIDGAME) is not None:
            self._pending_recommendation = None
            return
        opening_slot = self.board.slots.get(StageKind.OPENING)
        if opening_slot is None:
            self._pending_recommendation = None
            return

        try:
            from vibecraft.strategy.models import OpeningBuild

            strat = self.library.get(opening_slot.strategy_id)
            if not isinstance(strat, OpeningBuild) or not strat.phases:
                self._pending_recommendation = None
                return
            state = self.facade.get_state()
            current_phase = self._compute_current_phase_id(
                strat.phases,
                int(state.supply_used),
                float(state.game_time),
                self._phase_events,
            )
            if current_phase != strat.phases[-1].id:
                self._pending_recommendation = None
                return

            # opening 完成 → 找推荐。
            # 2026-05-30 用户:转型推荐改成本驱动 —— 不再写死 yaml default_transitions[0],
            # 改调 pick_best_persistent 算 6 分量迁移成本(建筑/科技/兵种/气/counter/沉没),
            # 在当前 race 所有 persistent doctrine 里选成本最低的一个推荐给玩家。
            # 据当前已有单位 / 科技 / 气矿动态选,phoenix→skytoss/phoenix_control/...
            # 不再永远推 iac_2base。
            cost_reco = self._cost_based_recommendation(strat)
            if cost_reco is not None:
                self._pending_recommendation = cost_reco
                return

            # cost 路径失败(race 没注册 persistent / library 空)→ fallback yaml 默认转
            if strat.default_transitions:
                target_mid = strat.default_transitions[0].midgame_id
                if (StageKind.MIDGAME, target_mid) in self._dismissed_recommendations:
                    self._pending_recommendation = None
                    return
                target_strat = self.library.get(target_mid)
                display = self._strat_display(target_strat, target_mid)
                self._pending_recommendation = Recommendation(
                    stage=StageKind.MIDGAME,
                    strategy_id=target_mid,
                    display_name=display,
                    reason=_i18n_t(
                        "strategy.autoTransition",
                        self._lang,
                        from_=self._strat_display(strat),
                        to=display,
                    ),
                    source="default",
                )
                return

            self._pending_recommendation = None
        except Exception:
            self._pending_recommendation = None

    def _cost_based_recommendation(self, opening_strat: Any) -> Recommendation | None:
        """按迁移成本选最低成本 persistent doctrine 作为转型推荐。

        复用 _apply_auto_persistent_switch 的成本基础设施（snapshot + enemy_tags +
        pick_best_persistent）。返回 None 表示成本路径不可用（race 没注册 persistent
        doctrine / library 空 / 被玩家忽略），调用方 fallback 到 yaml 默认转。
        """
        library = self.library if self.library is not None else self.parser.library
        if library is None:
            return None
        my_race = (self.parser.my_race or "").lower()
        if not my_race:
            return None

        from vibecraft.strategy.transition_cost import pick_best_persistent

        snapshot = self._build_game_snapshot_for_cost()
        enemy_tags = self._compute_enemy_tags()
        try:
            chosen, _cost, all_costs = pick_best_persistent(snapshot, enemy_tags, library, my_race)
        except ValueError:
            return None

        # 玩家已忽略过这条推荐 → 不再推（让调用方 fallback / 清空）
        if (StageKind.MIDGAME, chosen) in self._dismissed_recommendations:
            return None

        target_strat = library.get(chosen)
        display = self._strat_display(target_strat, chosen)
        opening_display = self._strat_display(
            opening_strat, _i18n_t("strategy.defaultOpening", self._lang)
        )
        # 列出前 2 个更贵的备选，附在理由里（玩家看到为什么选这个）
        sorted_costs = sorted(all_costs.items(), key=lambda kv: kv[1])
        alts = [self._strat_display(library.get(sid), sid) for sid, _ in sorted_costs[1:3]]
        alt_text = _i18n_t("strategy.altOptions", self._lang, alts=" / ".join(alts)) if alts else ""
        return Recommendation(
            stage=StageKind.MIDGAME,
            strategy_id=chosen,
            display_name=display,
            reason=_i18n_t(
                "strategy.costRecommend",
                self._lang,
                from_=opening_display,
                to=display,
                alts=alt_text,
            ),
            source="cost",
        )

    def confirm_recommendation(self, now: float) -> None:
        """玩家在 PWA 点 [确认] → 把 self._pending_recommendation submit 成 VOICE directive。

        用 VOICE 来源(不用 AUTO_TRANSITION):玩家显式认可了,等价 voice 命令。
        Submit 后立即 clear self._pending_recommendation,避免下个 snapshot 还推荐这一个。
        """
        reco = self._pending_recommendation
        if reco is None:
            return
        from vibecraft.directives.models import Directive, StrategySetPayload
        from vibecraft.directives.types import IssuedBy

        directive = Directive(
            payload=StrategySetPayload(stage=reco.stage.value, strategy_id=reco.strategy_id),
            issued_at=now,
            issued_by=IssuedBy.VOICE,  # 玩家确认 → 等价 voice
            source_text=f"confirm_recommendation:{reco.stage.value}→{reco.strategy_id}",
        )
        self._pending_recommendation = None
        self._submit_directives([directive], now)

    def dismiss_recommendation(self) -> None:
        """玩家在 PWA 点 [忽略] → 清掉当前推荐,并记入 dismissed 黑名单。

        后续 _update_recommendation 重新计算时跳过同 (stage, strategy_id),
        不再重复推这条。如果换了别的推荐(不同 strategy_id),仍会推新的。
        """
        if self._pending_recommendation is not None:
            r = self._pending_recommendation
            self._dismissed_recommendations.add((r.stage, r.strategy_id))
        self._pending_recommendation = None

    def _dispatch_event(self, ev: BoardEvent) -> None:
        # log 每个事件到 events.jsonl
        kind_map = {
            BoardEventKind.STRATEGY_CHANGED: EventKind.STRATEGY_SET,
            BoardEventKind.PHASE_TRANSITIONED: EventKind.STRATEGY_PHASE_CHANGE,
            BoardEventKind.RELEASED: EventKind.DIRECTIVE_RELEASED,
            BoardEventKind.REJECTED: EventKind.DIRECTIVE_FAILED,
            BoardEventKind.COMMITTED: EventKind.DIRECTIVE_COMMITTED,
            BoardEventKind.SUPERSEDED: EventKind.DIRECTIVE_RELEASED,
        }
        if ev.kind in kind_map:
            self.session.log_event(
                Event(
                    ts=ev.ts,
                    kind=kind_map[ev.kind],
                    payload={**ev.payload, "directive_id": ev.directive_id},
                    caused_by=ev.reason,
                )
            )

        # 同步写 directives.jsonl —— directive 生命周期全量（submitted 在 _submit_directives 写）
        _directive_lifecycle_kinds = (
            BoardEventKind.COMMITTED,
            BoardEventKind.RELEASED,
            BoardEventKind.REJECTED,
            BoardEventKind.REVOKED,
        )
        if ev.kind in _directive_lifecycle_kinds and ev.directive_id is not None:
            record: dict[str, object] = {
                "ts": round(ev.ts, 3),
                "event": ev.kind.value.split(".")[-1],  # "committed" / "released" / etc.
                "directive_id": ev.directive_id,
                **ev.payload,
            }
            if ev.reason is not None:
                record["reason"] = ev.reason
            self.session.log(LogStream.DIRECTIVES, record)

        # P1-1：A 组埋点 —— BoardEvent → event 帧 dict → _event_callback
        self._maybe_push_event_frame(ev)

        # 仅在 COMMITTED 时下发 facade 调用
        if ev.kind == BoardEventKind.COMMITTED and ev.directive_id is not None:
            self._dispatch_committed_to_facade(ev.directive_id, ev.ts)

    def _maybe_push_event_frame(self, ev: BoardEvent) -> None:
        """把 BoardEvent 转译成设计文档 §9.4 的 event 帧，推到手机（P1-1 A 组）。

        只转译有意义的 kind；SUBMITTED/REVOKED/SUPERSEDED 不推（信息量低）。
        """
        # §9.4 taxonomy 映射
        ws_kind_map: dict[BoardEventKind, str] = {
            BoardEventKind.STRATEGY_CHANGED: "strategy.set",
            BoardEventKind.PHASE_TRANSITIONED: "strategy.phase_change",
            BoardEventKind.COMMITTED: "directive.committed",
            BoardEventKind.RELEASED: "directive.released",
            BoardEventKind.REJECTED: "directive.rejected",
        }
        ws_kind = ws_kind_map.get(ev.kind)
        if ws_kind is None:
            return

        payload: dict[str, Any] = dict(ev.payload)
        if ev.directive_id is not None:
            payload["directive_id"] = ev.directive_id

        # strategy.set / strategy.phase_change：补 display（§2.5）
        if ev.kind == BoardEventKind.STRATEGY_CHANGED:
            sid = payload.get("strategy_id", "")
            if self.library is not None and isinstance(sid, str) and sid:
                try:
                    strat = self.library.get(sid)
                    payload["display"] = self._strat_display(strat, sid)
                except Exception:
                    pass

        event_dict = {
            "type": "event",
            "kind": ws_kind,
            "ts": round(ev.ts, 3),
            "payload": payload,
        }
        self._push_event(event_dict)

    def _dispatch_committed_to_facade(self, directive_id: str, now: float) -> None:
        d = self._in_flight.pop(directive_id, None)
        if d is None:
            # 已被 revoke / supersede；忽略
            return
        self._committed_count += 1
        # 2026-05-28 用户:activate_when 激活门。directive 有 activate_when 且
        # 条件未满足 → 不立即 _apply_to_facade,挂到 _pending_activation 队列,
        # on_tick 每 tick re-check;满足后才真激活。
        activate_when = getattr(d.payload, "activate_when", None)
        if activate_when is not None and not self._is_activation_satisfied(activate_when, d):
            self._pending_activation[directive_id] = d
            self._set_override_status(d, "waiting", _i18n_t("strategy.waitActivation", self._lang))
            logger.warning(
                "directive activation deferred: id=%s activate_when=%s",
                directive_id[:8],
                activate_when,
            )
            return
        # 激活条件已满足 → 走 normal path
        # 2026-05-25 bug 5:ephemeral directive commit 后保留(让 PWA 卡片
        # 在 task done/玩家 × 前一直可见)。standing_orders / production_overrides
        # 自己管,不重复加；TACTICAL_OBJECTIVE persistent 走 _current_l2_global_*
        # 路径,这里也不加(避免 active_tactics 重复)。
        from vibecraft.directives.models import (
            TacticalObjectivePayload,
            UnitClaimPayload,
        )

        is_persistent_unit_claim = isinstance(d.payload, UnitClaimPayload) and d.payload.persistent
        is_persistent_l2 = isinstance(d.payload, TacticalObjectivePayload) and d.payload.persistent
        if (
            d.type
            not in (
                DirectiveType.PRODUCTION_OVERRIDE,
                DirectiveType.TECH_OVERRIDE,
                DirectiveType.EXPANSION_OVERRIDE,
                DirectiveType.STRUCTURE_OVERRIDE,
                DirectiveType.DROP_ACT,
                DirectiveType.STRATEGY_SET,
                DirectiveType.STRATEGY_CANCEL,
            )
            and not is_persistent_unit_claim
            and not is_persistent_l2
        ):
            self._committed_directives[directive_id] = d
        # 兜底：单条 directive 执行抛异常绝不能冒泡到 sc2 主循环（会直接结束整局 match）。
        # 捕获 → 日志 + 卡片标"执行出错"，游戏继续（2026-06-03 用户：应报错不应崩）。
        try:
            self._apply_to_facade(d, now)
        except Exception as exc:
            logger.exception("directive 执行异常 (id=%s, type=%s)", directive_id[:8], d.type)
            with contextlib.suppress(Exception):
                self._set_override_status(
                    d, "on_hold", _i18n_t("err.execFail", self._lang, exc=exc)
                )

    def _apply_to_facade(self, d: Directive, now: float) -> None:
        payload = d.payload
        t = d.type

        if t == DirectiveType.STRATEGY_CANCEL:
            assert isinstance(payload, StrategyCancelPayload)
            self._apply_strategy_cancel(payload, now, directive_id=d.id)
            return

        if t == DirectiveType.STRATEGY_SET:
            assert isinstance(payload, StrategySetPayload)
            self.facade.set_build(payload.strategy_id)
            return

        if t == DirectiveType.PRODUCTION_OVERRIDE:
            assert isinstance(payload, ProductionOverridePayload)
            # 注:production_override 的真实执行在 **_exec_production_override**(每 tick 重评估,
            # 4362 的 tick 循环调),warp_at 折跃路由也在那处理(这里 commit 只走 set_production_override
            # 占位;_exec 每帧覆盖)。
            for item in payload.items:
                self.facade.set_production_override(
                    unit_type=item.unit_type,
                    count=item.count,
                    building_tag=payload.building_tag,
                )
            return

        if t == DirectiveType.TECH_OVERRIDE:
            assert isinstance(payload, TechOverridePayload)
            self.facade.set_tech_override(
                upgrade_id=payload.upgrade_id, building_tag=payload.building_tag
            )
            return

        if t == DirectiveType.EXPANSION_OVERRIDE:
            assert isinstance(payload, ExpansionOverridePayload)
            self.facade.set_expansion_override(payload.target_count)
            return

        if t == DirectiveType.ENGAGEMENT_CONSTRAINT:
            # P1b 向后兼容映射：ENGAGEMENT_CONSTRAINT → TacticalObjective(persistent=True)
            # 旧 jsonl 反序列化 / 现有测试走这里；新代码走 TacticalObjective 路径
            assert isinstance(payload, EngagementConstraintPayload)
            # stance "hold" / "free" 不是 TacticalVerb，fallback 到直接 set_engagement_stance
            stance = payload.stance
            if stance in ("defend", "retreat"):
                # 映射为持续 TacticalObjective（同时立即 override + 写 stance_override）
                _compat_payload = TacticalObjectivePayload(
                    verb=stance,  # stance ∈ {"defend","retreat"} checked above
                    target_area=None,
                    persistent=True,
                )
                self._exec_l2_global(d, _compat_payload)
            else:
                # "hold" / "free" 直接走 stance facade
                self.facade.set_engagement_stance(stance)
            return

        if t == DirectiveType.UNIT_CLAIM:
            assert isinstance(payload, UnitClaimPayload)
            self._apply_unit_claim(d, payload, now)
            return

        if t == DirectiveType.UNIT_RELEASE:
            assert isinstance(payload, UnitReleasePayload)
            # 2026-06-01 方案3:效果(set IDLE/ARMY + cancel scout)已在 submit 执行,
            # 这里(commit)只关单,不重复执行 → 消掉 submit/commit 时序不对称(原 bug:
            # claim 在 submit 立即生效,release 延到 commit 才执行 → 误伤瞭望塔 probe)。
            self._release_directive_done(d, now, reason="unit_release_executed")
            return

        if t == DirectiveType.BUILD_AT:
            assert isinstance(payload, BuildAtPayload)
            # 2026-05-24 用户:支持模糊地点(named_spot)。优先 point,fallback named_spot。
            point = payload.point
            if point is None and payload.named_spot:
                try:
                    from vibecraft.bot.named_spot import NamedSpotRegistry

                    reg = NamedSpotRegistry()
                    bot = getattr(self, "_bot", None)
                    if bot is not None:
                        resolved = reg.resolve(payload.named_spot, bot)
                        if resolved is not None:
                            point = (float(resolved.x), float(resolved.y))
                except Exception as exc:
                    logger.debug("build_at named_spot resolve fail: %s", exc)
            if point is None:
                logger.warning("build_at no point/named_spot resolved id=%s", d.id[:8])
                return
            # 2026-06-06 真局自验:链式代理建造(activate_when=chain_structure_ready)必须建在
            # **前一步产出的那个建筑(水晶)旁边**,不能独立 resolve named_spot —— 同名地点
            # (如"natural")在不同卡 resolve 可能给出不同坐标,gateway/VS 被送到离水晶很远、
            # 没能量场的地方 → PROTOSSBUILD 被游戏当场拒(无能量场)→ 每帧建不出来。改用链上
            # 那个建筑的真实位置当锚点(它一定有能量场,且和水晶同点)。
            aw = getattr(payload, "activate_when", None)
            chain_cid = None  # 链式建造的 chain_id(用于锚点 + 复用链上同一农民)
            if payload.by_probe:
                # 优先从 payload.chain_id 获取(水晶卡等第一步 by_probe,activate_when=unit_arrived,
                # 没有 chain_structure_ready 但仍属于同一条链)。其次从 chain_structure_ready 获取。
                chain_cid = getattr(payload, "chain_id", None)
                if chain_cid is None and getattr(aw, "kind", None) == "chain_structure_ready":
                    chain_cid = getattr(aw, "chain_id", None)
            if chain_cid is not None and getattr(aw, "kind", None) == "chain_structure_ready":
                s_tags = self._chain_structures.get(chain_cid, set()) if chain_cid else set()
                bot = getattr(self, "_bot", None)
                # **优先锚到能量源 PYLON**:VS/BG 必须在 Pylon 能量场内才能建。链上可能已有
                # 第1个 VS,若锚到它(离 Pylon ~6 格、在能量场边缘)→ find_placement 在能量场外
                # 找不到合法位 → 第2个 VS"找不到位置"卡住(真局复现)。锚到 Pylon 本体 →
                # find_placement 在能量场中心附近找,第2个 VS 也能放下。
                anchor_pt = None
                any_pt = None
                if bot is not None:
                    for st_tag in s_tags:
                        with contextlib.suppress(Exception):
                            s = bot.structures.find_by_tag(int(st_tag))
                            if s is None:
                                continue
                            pos = (float(s.position.x), float(s.position.y))
                            if any_pt is None:
                                any_pt = pos
                            if getattr(getattr(s, "type_id", None), "name", "") == "PYLON":
                                anchor_pt = pos
                                break
                if anchor_pt is None:
                    anchor_pt = any_pt
                # 仅当本卡没有被"水晶建好时刷新过的具体落点"(payload.point)时,才回退到锚点。
                # 已刷新(_assign_chain_followup_spots 写了 payload.point)→ 用那个错开的落点。
                if anchor_pt is not None and payload.point is None:
                    point = anchor_pt
            # 2026-06-01 Task E β:by_probe=True 走代理建造路径(不走 placement override)。
            # 2026-06-06 用户(方案A):整条链(去X→修水晶→水晶好了修bg)用同一农民。
            if payload.by_probe:
                # **链式建造必须复用链上那个农民**(card0 claim 的、_task_chains 绑的)——
                # 不能重新 _pick:原农民漂走时 _pick 会另选一个自由农民 → 一条链两个农民,
                # standby 管 A、build 管 B,互相打架,第2个建筑永远建不出来(真局自验根因)。
                probe_tag = None
                if chain_cid is not None:
                    chain_tags = self._task_chains.get(chain_cid, set())
                    probe_tag = next(iter(chain_tags), None) if chain_tags else None
                if probe_tag is None:
                    probe_tag = self._pick_proxy_build_probe(point)
                if probe_tag is None:
                    logger.warning(
                        "build_at by_probe: no worker found near %s id=%s", point, d.id[:8]
                    )
                    return
                # 代理建造农民必须脱离 bot 控制,否则走去建造点途中(还没"正在建造")被
                # DistributeWorkers 当空闲 worker 拉回采矿 → 与 build 命令每帧打架(玩家观感:
                # 农民反复被拉扯,到不了)。
                # _pick 已优先选"已被指令持有的农民"(= card1 认领的/上一步那个);若它已被
                # 某卡持有 → 沿用不重复登记(那张卡持有到玩家×);否则本卡自己持有它。
                if self._current_owner_of(probe_tag, exclude_id=d.id) is None:
                    self.facade.set_unit_role(probe_tag, UnitRole.LLM_CONTROLLED)
                    self._standing_order_tags[d.id] = {probe_tag}
                self.facade.order_probe_build(
                    probe_tag, payload.structure_type, point, cache_key=d.id
                )
                # 进代理建造 tick:农民空闲且没建好就重发 build。**不自动放归** ——
                # 建完继续待命,直到玩家点 × / "释放"(2026-06-06 用户)。
                # 2026-06-07 修误判:快照此刻已有的同类型建筑 tag。settle 只认"新出现的"建筑,
                # 否则农民从家出发、途经家里已有的同类建筑(如虚空开矿家里 2 个 VS)时,
                # closer_than(3.5,农民位置)命中旧建筑 → 秒判"造好"完成(玩家报:在某点造 VS
                # 卡片秒变已完成,VS 根本没造)。
                preexisting_tags: set[int] = set()
                with contextlib.suppress(Exception):
                    from sc2.ids.unit_typeid import UnitTypeId as _UT

                    _tid0 = getattr(_UT, str(payload.structure_type).upper(), None)
                    if _tid0 is not None:
                        preexisting_tags = {int(s.tag) for s in self._bot.structures(_tid0)}
                self._pending_proxy_build[d.id] = {
                    "tag": probe_tag,
                    "point": point,
                    "structure": payload.structure_type,
                    "since": float(getattr(self._bot, "time", 0.0)),
                    "preexisting": preexisting_tags,
                    # chain_id:settle 时直接用此值反查 chain,不靠 builder tag 查 _task_chains
                    # (builder 可能是链外农民,用 tag 反查 → cid=None → followup 不触发)。
                    "chain_id": chain_cid,
                }
                # 摘掉 task_monitor:其 done_when(到位/超时)会在农民还在走时误判完成、提前
                # release → 农民被放归 → 又被拉扯。生命周期改由 _tick_proxy_build + 玩家× 管。
                if self.task_monitor is not None:
                    with contextlib.suppress(Exception):
                        self.task_monitor.detach(d.id)
                return
            self.facade.set_build_location_override(payload.structure_type, point)
            return

        if t == DirectiveType.MOVE:
            assert isinstance(payload, MovePayload)
            # 2026-05-25 bug 4:走 helper 截断 sel.count(否则 LLM count=1 但
            # MOVE 这里漏 cap → 全军同 unit_type 单位被 move_to target)。
            tags = self._resolve_selector_with_count(payload.selector)
            # 2026-05-27 用户 Issue 3:玩家"出 X 然后让 X 去 Y" 复合指令。
            # production_override + move 同帧 commit,X 还在 produce 中 → tags=[]。
            # 修前:safe_move tick 看 empty tags → directive 立刻 mark done,棱镜出来
            # 也不走;safe=False 直接跳过 for tag,move 永不发。
            # 修后:tags=[] + selector 有 unit_type → 进 _pending_move,每 tick re-resolve,
            # unit 出现接管为 safe_move/直接派 move;90s timeout 放弃 release。
            sel = payload.selector
            if not tags and sel is not None and sel.unit_type:
                self._pending_move[d.id] = {
                    "selector": sel,
                    "target_dict": payload.target.model_dump(mode="json"),
                    "safe": payload.safe,
                    "engage": payload.engage,
                    "submitted_at": float(getattr(self._bot, "time", 0.0)),
                }
                logger.warning(
                    "MOVE pending: no units yet for selector=%s,等 unit 出现(id=%s)",
                    sel.unit_type,
                    d.id[:8],
                )
                return
            # 2026-05-24 用户:safe=True 走 plan_drop_path 避敌(_tick_move_orders 控位);
            # safe=False 走原直线 facade.execute_unit_action(直接 move 一次)。
            if payload.safe:
                self._safe_move_tags[d.id] = (
                    set(tags),
                    payload.target.model_dump(mode="json"),
                    payload.engage,
                )
            else:
                move_verb = "attack_move" if payload.engage else "move_to"
                for tag in tags:
                    self.facade.execute_unit_action(
                        unit_tag=tag, verb=move_verb, target=payload.target.model_dump(mode="json")
                    )
            return

        if t == DirectiveType.RALLY_POINT:
            assert isinstance(payload, RallyPointPayload)
            # 出兵集结点(2026-06-07 用户):设全局 rally。target=camera 已被 _inject_camera_point
            # 注入成 point。解析成 (x,y) 存 _rally_point;on_tick 每帧 facade.set_rally_point 覆盖
            # sharpy gather_point(一次性 flag 必须每帧)。单条生效:旧 rally 卡标 done(被覆盖)。
            pt = self._resolve_target_spec_point(payload.target)
            if pt is None:
                logger.warning("rally_point 解析不出坐标 (id=%s)", d.id[:8])
                self._set_override_status(d, "on_hold", _i18n_t("rally.noPoint", self._lang))
                return
            # 覆盖旧 rally:旧卡标 done 消失
            if self._rally_point_id and self._rally_point_id != d.id:
                old = self._committed_directives.get(self._rally_point_id) or self._in_flight.get(
                    self._rally_point_id
                )
                if old is not None:
                    with contextlib.suppress(Exception):
                        self._set_override_status(
                            old, "done", _i18n_t("rally.superseded", self._lang)
                        )
                    self._done_at[self._rally_point_id] = now
            self._rally_point = pt
            self._rally_point_id = d.id
            # directive 已在 _in_flight(submit 路由),卡片/撤销从那查;不重复存 committed。
            with contextlib.suppress(Exception):
                self.facade.set_rally_point(pt)  # 立刻生效一帧(on_tick 后续每帧续)
            self._set_override_status(d, "active", "")
            logger.info("rally_point set → (%.1f,%.1f) id=%s", pt[0], pt[1], d.id[:8])
            return

        if t == DirectiveType.SCOUT:
            assert isinstance(payload, ScoutPayload)
            sel = payload.selector
            # SCOUT 默认单单位（"派一个农民去探路"）。
            # 走 helper 截断:LLM 显式 count=N → 取 N 个;count=None + 只给
            # unit_type → 取 1 个(2026-05-18 bug "一个农民探路所有农民都出去")。
            if sel and sel.tag is not None:
                tags = [sel.tag]
            elif sel and sel.tags:
                tags = list(sel.tags)
            elif sel and sel.count is not None and sel.count > 0:
                tags = self._resolve_selector_with_count(sel)
            else:
                resolved = self.facade.resolve_selector(
                    unit_type=(sel.unit_type if sel else None),
                )
                tags = resolved[:1]  # 只取第一个(unit_type only + 没 count)
            if not tags:
                # fallback：让 facade 自选 idle probe（tag=0 占位）
                self.facade.execute_unit_action(
                    unit_tag=0,
                    verb="scout",
                    target=payload.target.model_dump(mode="json"),
                )
            else:
                for tag in tags:
                    self.facade.execute_unit_action(
                        unit_tag=tag,
                        verb="scout",
                        target=payload.target.model_dump(mode="json"),
                    )
            return

        if t == DirectiveType.STRUCTURE_OVERRIDE:
            assert isinstance(payload, StructureOverridePayload)
            # production_overrides list 的路由已在 _submit_directives 做；
            # _apply_to_facade 不需额外 facade 调用（UI 透传走 snapshot 路径）。
            return

        if t == DirectiveType.DROP_ACT:
            # DROP_ACT 走 execute_overrides_step 每 tick 处理；
            # _apply_to_facade 不额外调用 — 路由已在 _submit_directives 做。
            return

        if t == DirectiveType.VIEW_FOLLOW:
            assert isinstance(payload, ViewFollowPayload)
            self._apply_view_follow(d, payload, now)
            return

        if t == DirectiveType.PRODUCTION_BLOCK:
            assert isinstance(payload, ProductionBlockPayload)
            self._apply_production_block(d, payload, now)
            return

        if t == DirectiveType.TACTICAL_OBJECTIVE:
            assert isinstance(payload, TacticalObjectivePayload)
            self._exec_tactical_objective(d, payload)
            return

        if t in (DirectiveType.GROUP_ASSIGN, DirectiveType.GROUP_CLEAR):
            # 2026-06-01 语音编队：效果已在 _submit_directives 执行；
            # _apply_to_facade 不重复执行（no-op）。
            return

        if t == DirectiveType.STEALTH_MINE:
            # 2026-06-10 WP1 偷矿：commit 时创建 PENDING cell，由 StealthCellManager 驱动。
            # payload.cell_id=0（占位）；Manager 分配真实 id 并返回。
            from vibecraft.directives.models import StealthMinePayload

            assert isinstance(payload, StealthMinePayload)

            # 种族检查：偷矿系统当前只支持神族（NEXUS/PROBE 写死）
            # 非 Protoss → 友好拒绝，不建 cell，不抛异常（2026-06-12 用户反馈 #6）
            _my_race = (getattr(self.parser, "my_race", None) or "").lower()
            if _my_race and _my_race != "protoss":
                logger.warning(
                    "stealth_mine_race_rejected: race=%s directive_id=%s",
                    _my_race,
                    d.id[:8],
                )
                self._set_override_status(d, "failed", _i18n_t("stealth.protossOnly", self._lang))
                return

            cell_id = self._stealth_manager.create_cell(payload)
            # 建立双向映射，供 command_cards 查农民数 + release 时清 directive 卡
            self._directive_to_cell_id[d.id] = cell_id
            self._cell_id_to_directive_id[cell_id] = d.id
            self._set_override_status(
                d, "active", _i18n_t("stealth.cellCreated", self._lang, cell_id=cell_id)
            )
            logger.info(
                "STEALTHTRACE stealth_mine_applied directive_id=%s cell_id=%d point=(%.1f,%.1f)",
                d.id[:8],
                cell_id,
                payload.point[0],
                payload.point[1],
            )
            return

        if t == DirectiveType.SALVAGE:
            # 2026-06-19 通用建筑回收：对选中建筑下 salvage ability，一次性动作。
            # 地堡有乘员 → 先 UNLOADALL_BUNKER，加入 _pending_salvage_tags，
            # 等 _tick_pending_salvage 确认空了再发 SALVAGEEFFECT_SALVAGE。
            assert isinstance(payload, SalvagePayload)
            tags = self._resolve_selector_with_count(payload.selector)
            salvaged = 0
            deferred = 0
            unsalvageable: list[str] = []
            for tag in tags:
                type_name = self.facade.get_unit_type_name(tag)
                if type_name is None:
                    unsalvageable.append(f"tag={tag}")
                    continue
                abilities = _SALVAGE_ABILITIES.get(type_name.upper())
                if abilities is None:
                    unsalvageable.append(type_name)
                    continue
                # 地堡有乘员 → 先卸载再回收（SC2 拒绝回收带兵地堡）
                if type_name.upper() == "BUNKER" and self.facade.bunker_has_cargo(tag):
                    self.facade.cast_unit_ability(tag, "UNLOADALL_BUNKER")
                    self._pending_salvage_tags.add(tag)
                    deferred += 1
                    logger.info(
                        "SALVAGETRACE salvage_deferred tag=%d reason=has_cargo",
                        tag,
                    )
                    continue
                for ability_id in abilities:
                    self.facade.cast_unit_ability(tag, ability_id)
                salvaged += 1
            parts: list[str] = []
            if salvaged:
                parts.append(_i18n_t("salvage.collected", self._lang, n=salvaged))
            if deferred:
                parts.append(_i18n_t("salvage.unloading", self._lang, n=deferred))
            if unsalvageable:
                names = ", ".join(dict.fromkeys(unsalvageable))  # 去重保序
                parts.append(_i18n_t("salvage.unsupported", self._lang, names=names))
            reason = "；".join(parts) if parts else _i18n_t("salvage.noneAvailable", self._lang)
            status = "done" if (salvaged > 0 or deferred > 0) else "failed"
            self._set_override_status(d, status, reason)
            logger.info(
                "SALVAGETRACE salvage_applied directive_id=%s salvaged=%d"
                " deferred=%d unsalvageable=%r",
                d.id[:8],
                salvaged,
                deferred,
                unsalvageable,
            )
            return

        if t == DirectiveType.BUNKER_CARGO:
            # 2026-06-19 地堡货舱控制：装兵（load）或卸载（unload）。
            assert isinstance(payload, BunkerCargoPayload)
            tags = self._resolve_selector_with_count(payload.selector)
            acted = 0
            action = payload.action
            count = payload.count if payload.count is not None else 4
            for tag in tags:
                type_name = self.facade.get_unit_type_name(tag)
                if type_name is None or type_name.upper() != "BUNKER":
                    logger.info(
                        "BUNKERCARGOTRACE skip tag=%d type=%s (not a bunker)",
                        tag,
                        type_name,
                    )
                    continue
                if action == "unload":
                    self.facade.cast_unit_ability(tag, "UNLOADALL_BUNKER")
                    acted += 1
                    logger.info("BUNKERCARGOTRACE unload_issued tag=%d", tag)
                elif action == "load":
                    loaded = self.facade.load_bunker(tag, count)
                    acted += loaded
                    logger.info("BUNKERCARGOTRACE load_issued tag=%d loaded=%d", tag, loaded)
            if acted > 0:
                action_zh = (
                    _i18n_t("bunker.unload", self._lang)
                    if action == "unload"
                    else _i18n_t("bunker.load", self._lang)
                )
                self._set_override_status(
                    d, "done", _i18n_t("bunker.acted", self._lang, n=acted, action=action_zh)
                )
            else:
                self._set_override_status(d, "failed", _i18n_t("bunker.notFound", self._lang))
            logger.info(
                "BUNKERCARGOTRACE bunker_cargo_applied directive_id=%s action=%s acted=%d",
                d.id[:8],
                action,
                acted,
            )
            return

        if t == DirectiveType.REPAIR:
            # 2026-06-19 通用维修指令：持续型，存入 _repair_orders，每 tick 由
            # _tick_repair_orders 检查血量派 SCV 维修；全部满血/消失才标 done。
            assert isinstance(payload, RepairPayload)
            self._repair_orders[d.id] = d
            self._set_override_status(
                d, _i18n_t("status.repairing", self._lang), _i18n_t("repair.dispatched", self._lang)
            )
            logger.info(
                "REPAIRTRACE repair_registered directive_id=%s selector=%r worker_count=%s",
                d.id[:8],
                payload.selector,
                payload.worker_count,
            )
            return

        if t == DirectiveType.STRUCTURE_MOVE:
            # 2026-07-08 人族建筑起飞/移动：持续型，存入 _structure_move_orders，
            # 每 tick(async)由 _tick_structure_move 推进 FIND→LIFT→(悬停 done / FLY→LAND)。
            assert isinstance(payload, StructureMovePayload)
            self._structure_move_orders[d.id] = {"directive": d}
            self._set_override_status(d, "active", _i18n_t("structmove.prep", self._lang))
            logger.info(
                "STRUCTUREMOVETRACE registered directive_id=%s from=%s to=%s",
                d.id[:8],
                payload.from_spot,
                payload.to_spot,
            )
            return

        if t == DirectiveType.WORKER_TASK:
            # 2026-07-08 农民基地调度：prioritize_* 直接调全局 set_mining_priority；
            # transfer_to_base 一次性选中农民后交给 _tick_worker_task_transfer 持续钉住。
            assert isinstance(payload, WorkerTaskPayload)
            self._exec_worker_task(d, payload, now)
            return

    def _tick_pending_salvage(self, now: float) -> None:
        """每 tick 检查 _pending_salvage_tags：地堡乘员清空后发 SALVAGEEFFECT_SALVAGE 回收。

        状态机：
          SALVAGE 分支 → has_cargo → UNLOADALL_BUNKER + 加 pending（本 tick 不拆）
          每 tick → 乘员 False → 发 SALVAGEEFFECT_SALVAGE + 从 pending 移除
          每 tick → 建筑消失（get_unit_type_name=None）→ 移除（被打掉或已拆）
        """
        if not self._pending_salvage_tags:
            return
        done: list[int] = []
        for tag in list(self._pending_salvage_tags):
            type_name = self.facade.get_unit_type_name(tag)
            if type_name is None:
                # 建筑已不存在（被攻击打掉或已拆除）
                done.append(tag)
                logger.info(
                    "SALVAGETRACE pending_salvage_gone tag=%d game_time=%.1f",
                    tag,
                    now,
                )
                continue
            if not self.facade.bunker_has_cargo(tag):
                # 乘员已清空 → 现在可以回收
                self.facade.cast_unit_ability(tag, "SALVAGEEFFECT_SALVAGE")
                done.append(tag)
                logger.info(
                    "SALVAGETRACE pending_salvage_fired tag=%d game_time=%.1f",
                    tag,
                    now,
                )
        for tag in done:
            self._pending_salvage_tags.discard(tag)

    def _tick_repair_orders(self, now: float) -> None:
        """每 tick 检查 _repair_orders：对每个目标派 SCV 维修；全部满血/消失 → done。

        状态机：
          REPAIR 分支 → 存入 _repair_orders，status="维修中"
          每 tick → 检查血量；有损伤 → ensure_repair 派 SCV；全部满血/消失 → done + 移除
        """
        if not self._repair_orders:
            return
        done_ids: list[str] = []
        for d_id, directive in list(self._repair_orders.items()):
            payload = directive.payload
            assert isinstance(payload, RepairPayload)
            worker_count = payload.worker_count or 3
            tags = self._resolve_selector_with_count(payload.selector)
            if not tags:
                # selector 找不到任何目标（全消失/从未存在）
                self._set_override_status(
                    directive, "done", _i18n_t("repair.targetGone", self._lang)
                )
                done_ids.append(d_id)
                logger.info("REPAIRTRACE repair_done_no_targets directive_id=%s", d_id[:8])
                continue
            all_healthy = True
            for tag in tags:
                hp = self.facade.get_unit_health_percentage(tag)
                if hp is None:
                    continue  # 该目标已消失，跳过
                if hp < 0.99:
                    all_healthy = False
                    dispatched = self.facade.ensure_repair(tag, worker_count)
                    logger.info(
                        "REPAIRTRACE repair_dispatched directive_id=%s tag=%d"
                        " workers=%d hp=%.2f game_time=%.1f",
                        d_id[:8],
                        tag,
                        dispatched,
                        hp,
                        now,
                    )
            if all_healthy:
                self._set_override_status(
                    directive, "done", _i18n_t("repair.allHealthy", self._lang)
                )
                done_ids.append(d_id)
                logger.info(
                    "REPAIRTRACE repair_done_all_healthy directive_id=%s game_time=%.1f",
                    d_id[:8],
                    now,
                )
        for d_id in done_ids:
            self._repair_orders.pop(d_id, None)

    # ------------------------------------------------------------------
    # 2026-07-08 WORKER_TASK：农民基地调度
    # ------------------------------------------------------------------

    def _exec_worker_task(self, d: Directive, payload: WorkerTaskPayload, now: float) -> None:
        """WORKER_TASK 分发：prioritize_* 直接调全局 mining_priority；transfer_to_base
        选中 from_base 附近全部采矿农民，Reserve 住持续钉去 to_base 采矿数秒。
        """
        from_pt = self._resolve_target_area(payload.from_base)
        if from_pt is None:
            self._set_override_status(d, "on_hold", _i18n_t("workertask.noFromBase", self._lang))
            return

        if payload.action in ("prioritize_minerals", "prioritize_gas"):
            # 2026-07-08 评审点3:复用全局 set_mining_priority(宏观面板 mining 维度
            # 同一个开关),不做 per-base 隔离(YAGNI + 避免第二 patch 点冲突)。
            priority = "mineral" if payload.action == "prioritize_minerals" else "gas"
            self.facade.set_mining_priority(priority)
            self._set_override_status(d, "active", _i18n_t("workertask.prioritized", self._lang))
            logger.info(
                "WORKERTASKTRACE prioritize_applied directive_id=%s action=%s",
                d.id[:8],
                payload.action,
            )
            return

        # transfer_to_base
        if payload.to_base is None:
            self._set_override_status(d, "failed", _i18n_t("workertask.noToBase", self._lang))
            return
        to_pt = self._resolve_target_area(payload.to_base)
        if to_pt is None:
            self._set_override_status(d, "on_hold", _i18n_t("workertask.noToBase", self._lang))
            return
        tags = self._select_mining_workers_near(from_pt)
        if not tags:
            self._set_override_status(d, "failed", _i18n_t("workertask.noWorkers", self._lang))
            logger.info("WORKERTASKTRACE transfer_no_workers directive_id=%s", d.id[:8])
            return
        to_point = (float(to_pt.x), float(to_pt.y))
        for tag in tags:
            # Reserve 住:防 sharpy DistributeWorkers 同帧把令覆盖回去(见 #543 同款
            # _refresh_llm_controlled_roles 每帧 re-Reserve 机制)。settle 后 release 归还。
            with contextlib.suppress(Exception):
                self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            self.facade.order_worker_gather(tag, to_point)
        self._worker_task_transfer_orders[d.id] = {
            "directive": d,
            "tags": list(tags),
            "to_point": to_point,
            "expire_at": now + self._WORKER_TRANSFER_SETTLE_S,
        }
        self._set_override_status(
            d, "active", _i18n_t("workertask.transferring", self._lang, n=len(tags))
        )
        logger.info(
            "WORKERTASKTRACE transfer_started directive_id=%s n=%d to=(%.1f,%.1f)",
            d.id[:8],
            len(tags),
            to_point[0],
            to_point[1],
        )

    def _select_mining_workers_near(self, point: Any, radius: float = 12.0) -> list[int]:
        """point 附近正在采矿(Gathering role,非采气,非在建)的农民 tag 列表(WORKER_TASK 用)。

        过滤三条(2026-07-08 评审点4)：role==Gathering(排除 Reserved/Building 等其它
        task)、非 is_carrying_vespene、order target 不在 gas_buildings tags 内(排除
        正在采气但尚未 carry 的农民)。
        """
        if self._bot is None:
            return []
        try:
            from sharpy.managers.core.roles.unit_task import UnitTask
        except Exception as exc:
            logger.debug("_select_mining_workers_near UnitTask import fail: %s", exc)
            return []
        try:
            gas_tags = {int(g.tag) for g in self._bot.gas_buildings}
            workers = self._bot.workers.closer_than(radius, point)
            result: list[int] = []
            for w in workers:
                try:
                    role = self._bot.knowledge.roles.unit_role(w)
                except Exception:
                    continue
                if role != UnitTask.Gathering:
                    continue
                if getattr(w, "is_carrying_vespene", False):
                    continue
                orders = getattr(w, "orders", None)
                if orders:
                    tgt = getattr(orders[0], "target", None)
                    if isinstance(tgt, int) and tgt in gas_tags:
                        continue
                result.append(int(w.tag))
            return result
        except Exception as exc:
            logger.debug("_select_mining_workers_near fail: %s", exc)
            return []

    def _tick_worker_task_transfer(self, now: float) -> None:
        """每 tick 检查 _worker_task_transfer_orders：settle 期内持续重发 gather 令
        (对抗 DistributeWorkers 拉回)；到期或农民全消失 → release 释放归还 bot。
        """
        if not self._worker_task_transfer_orders:
            return
        done_ids: list[str] = []
        for d_id, state in list(self._worker_task_transfer_orders.items()):
            directive = state["directive"]
            to_point = state["to_point"]
            tags: list[int] = state["tags"]
            if now >= state["expire_at"]:
                # 终态自验证据(2026-07-08 CLAUDE.md「验终态别只验 trace」)：settle 到期时
                # 逐个农民读 facade.get_unit_position(真实引擎坐标,不是我方 intent)算距目标点
                # 距离并打日志 —— 自验脚本据此判"农民真的到了",不是"我们下过令"。
                for tag in tags:
                    pos = self.facade.get_unit_position(tag)
                    if pos is not None:
                        dist = ((pos[0] - to_point[0]) ** 2 + (pos[1] - to_point[1]) ** 2) ** 0.5
                        logger.info(
                            "WORKERTASKTRACE transfer_worker_pos directive_id=%s tag=%d"
                            " pos=(%.1f,%.1f) dist_to_target=%.1f",
                            d_id[:8],
                            tag,
                            pos[0],
                            pos[1],
                            dist,
                        )
                    with contextlib.suppress(Exception):
                        self.facade.release_unit_role(tag)
                self._set_override_status(
                    directive, "done", _i18n_t("workertask.transferred", self._lang, n=len(tags))
                )
                done_ids.append(d_id)
                logger.info(
                    "WORKERTASKTRACE transfer_done directive_id=%s n=%d", d_id[:8], len(tags)
                )
                continue
            alive: list[int] = []
            for tag in tags:
                pos = self.facade.get_unit_position(tag)
                if pos is None:
                    continue  # 死亡/消失
                alive.append(tag)
                self.facade.order_worker_gather(tag, to_point)
            state["tags"] = alive
            if not alive:
                self._set_override_status(
                    directive, "done", _i18n_t("workertask.transferredGone", self._lang)
                )
                done_ids.append(d_id)
                logger.info("WORKERTASKTRACE transfer_all_gone directive_id=%s", d_id[:8])
        for d_id in done_ids:
            self._worker_task_transfer_orders.pop(d_id, None)

    # ------------------------------------------------------------------
    # 2026-07-08 STRUCTURE_MOVE：人族建筑起飞/移动
    # ------------------------------------------------------------------

    def _find_nearest_townhall(self, point: Any) -> Any | None:
        """point 附近最近的人族 townhall：CommandCenter/OrbitalCommand/PlanetaryFortress
        **∪ 对应 *FLYING 飞行变体**(2026-07-08 用户补充1：要支持对"已在飞的"基地下新
        指令，如"基地飞到三矿"/"降落在这里"——FIND 不能只找落地的)。PlanetaryFortress
        无飞行变体(不能起飞)，不纳入。
        """
        if self._bot is None:
            return None
        from sc2.ids.unit_typeid import UnitTypeId

        names = (
            "COMMANDCENTER",
            "ORBITALCOMMAND",
            "PLANETARYFORTRESS",
            "COMMANDCENTERFLYING",
            "ORBITALCOMMANDFLYING",
        )
        candidates: list[Any] = []
        for name in names:
            try:
                tid = UnitTypeId[name]
            except KeyError:
                continue
            candidates.extend(list(self._bot.structures(tid)))
        if not candidates:
            return None
        return min(candidates, key=lambda u: u.position.distance_to(point))

    async def _find_structure_land_spot(self, type_id: Any, near: Any) -> Any | None:
        """找 type_id(townhall,无挂件)可降落的位置 —— **必须优先贴矿最优采矿位**
        (2026-07-08 用户补充2：不能随便落一个 can_place 空点)。

        锚点 = `closest_expansion_location(near, bot)`(离 near 最近的扩张标准
        townhall 格位；无扩张数据 fallback 用 near 本身)。先试锚点本身，被占/放不下
        才由近及远网格扫（同 #543 `_find_relocate_spot`，townhall 无挂件不需挂件位
        检查；扫描锚点是**贴矿最优位**而不是原始 near，退化时也尽量贴近那片矿区）。
        """
        if self._bot is None:
            return None
        from sc2.position import Point2

        from vibecraft.bot.named_spot import closest_expansion_location

        try:
            anchor = closest_expansion_location(near, self._bot) or near
            if await self._bot.can_place_single(type_id, anchor):
                return anchor
            for radius in range(2, 17, 2):
                for dx in range(-radius, radius + 1, 2):
                    for dy in range(-radius, radius + 1, 2):
                        if max(abs(dx), abs(dy)) != radius:
                            continue
                        p = anchor.offset(Point2((float(dx), float(dy))))
                        if await self._bot.can_place_single(type_id, p):
                            return p
            return None
        except Exception as exc:
            logger.debug("_find_structure_land_spot fail: %s", exc)
            return None

    async def _tick_structure_move(self, now: float) -> None:
        """每 tick(async)推进 _structure_move_orders 状态机：FIND→LIFT→(悬停 done /
        FLY→LAND)。复用 #543 `_build_addon_on_parent` 同款套路:tag 缓存追踪(飞行
        变体换 type_id 不换 tag)、落点一次锁定别每帧重选。
        """
        if not self._structure_move_orders or self._bot is None:
            return
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId

        bot = self._bot
        done_ids: list[str] = []
        for d_id, state in list(self._structure_move_orders.items()):
            directive = state["directive"]
            payload = directive.payload
            if not isinstance(payload, StructureMovePayload):
                done_ids.append(d_id)
                continue

            tag = state.get("tag")
            parent_name = state.get("parent_name")

            # FIND(只做一次):定位 from_spot 最近的 townhall,按其真实 type_id 解析
            # ability(评审点1:structure_type 只作提示,不硬绑;PlanetaryFortress 拒绝)。
            if tag is None:
                from_pt = self._resolve_target_area(payload.from_spot)
                if from_pt is None:
                    self._set_override_status(
                        directive, "on_hold", _i18n_t("structmove.noFromSpot", self._lang)
                    )
                    continue
                townhall = self._find_nearest_townhall(from_pt)
                if townhall is None:
                    self._set_override_status(
                        directive, "failed", _i18n_t("structmove.noTownhall", self._lang)
                    )
                    done_ids.append(d_id)
                    continue
                # 2026-07-08 用户补充1:FIND 现在也会找到已在飞的(*FLYING),
                # real_name 要 strip 掉 FLYING 后缀才是 LIFT_/LAND_ ability 的真名。
                raw_name = str(townhall.type_id.name)
                already_flying = bool(getattr(townhall, "is_flying", False))
                real_name = raw_name[: -len("FLYING")] if raw_name.endswith("FLYING") else raw_name
                if real_name == "PLANETARYFORTRESS":
                    self._set_override_status(
                        directive, "failed", _i18n_t("structmove.planetaryNoFly", self._lang)
                    )
                    done_ids.append(d_id)
                    logger.info(
                        "STRUCTUREMOVETRACE rejected_planetary directive_id=%s tag=%d",
                        d_id[:8],
                        int(townhall.tag),
                    )
                    continue
                try:
                    lift_ab = AbilityId[f"LIFT_{real_name}"]
                    land_ab = AbilityId[f"LAND_{real_name}"]
                    UnitTypeId[f"{real_name}FLYING"]
                except KeyError:
                    self._set_override_status(
                        directive, "failed", _i18n_t("structmove.cannotFly", self._lang)
                    )
                    done_ids.append(d_id)
                    continue
                tag = int(townhall.tag)
                parent_name = real_name
                state["tag"] = tag
                state["parent_name"] = parent_name
                state["lift_ability"] = lift_ab.name
                state["land_ability"] = land_ab.name
                if already_flying and payload.to_spot is None:
                    # 已经在飞 + 玩家又说"起飞"(无新目标) → 已达成,直接 done。
                    self._set_override_status(
                        directive, "done", _i18n_t("structmove.hovering", self._lang)
                    )
                    done_ids.append(d_id)
                    logger.info(
                        "STRUCTUREMOVETRACE already_flying_hover directive_id=%s tag=%d",
                        d_id[:8],
                        tag,
                    )
                    continue
                # 已在飞 + 给了新 to_spot → 跳过 LIFT 直接进 landing;否则走 lift 阶段。
                state["phase"] = "landing" if already_flying else "lift"
                logger.info(
                    "STRUCTUREMOVETRACE found directive_id=%s tag=%d type=%s already_flying=%s",
                    d_id[:8],
                    tag,
                    parent_name,
                    already_flying,
                )

            phase = state.get("phase", "lift")
            try:
                parent_id = UnitTypeId[parent_name]
                flying_id = UnitTypeId[f"{parent_name}FLYING"]
            except KeyError:
                self._set_override_status(
                    directive, "failed", _i18n_t("structmove.cannotFly", self._lang)
                )
                done_ids.append(d_id)
                continue

            unit = bot.structures(parent_id).find_by_tag(tag)
            if unit is None:
                unit = bot.structures(flying_id).find_by_tag(tag)
            if unit is None:
                self._set_override_status(
                    directive, "failed", _i18n_t("structmove.gone", self._lang)
                )
                done_ids.append(d_id)
                logger.info("STRUCTUREMOVETRACE gone directive_id=%s tag=%d", d_id[:8], tag)
                continue

            is_flying = bool(getattr(unit, "is_flying", False))
            lift_ability_name = state["lift_ability"]
            land_ability_name = state["land_ability"]

            if phase == "lift":
                if not is_flying:
                    # 幂等重发(idempotent):没起飞就每帧重发 LIFT 直到状态切飞。
                    self.facade.cast_unit_ability(tag, lift_ability_name)
                    self._set_override_status(
                        directive, "active", _i18n_t("structmove.lifting", self._lang)
                    )
                    continue
                # 已在飞
                if payload.to_spot is None:
                    self._set_override_status(
                        directive, "done", _i18n_t("structmove.hovering", self._lang)
                    )
                    done_ids.append(d_id)
                    logger.info(
                        "STRUCTUREMOVETRACE hover_done directive_id=%s tag=%d", d_id[:8], tag
                    )
                    continue
                state["phase"] = "landing"
                phase = "landing"

            # phase == "landing"
            if not is_flying:
                # 已落地(降落完成)
                self._set_override_status(
                    directive, "done", _i18n_t("structmove.landed", self._lang)
                )
                done_ids.append(d_id)
                logger.info("STRUCTUREMOVETRACE landed directive_id=%s tag=%d", d_id[:8], tag)
                continue

            land_point = state.get("land_point")
            if land_point is None:
                to_pt = self._resolve_target_area(payload.to_spot)
                if to_pt is None:
                    self._set_override_status(
                        directive, "on_hold", _i18n_t("structmove.noToSpot", self._lang)
                    )
                    continue
                land_point = await self._find_structure_land_spot(parent_id, to_pt)
                if land_point is None:
                    self._set_override_status(
                        directive, "active", _i18n_t("structmove.noLandSpot", self._lang)
                    )
                    continue
                state["land_point"] = land_point
                logger.info(
                    "STRUCTUREMOVETRACE land_point directive_id=%s point=(%.1f,%.1f)",
                    d_id[:8],
                    land_point.x,
                    land_point.y,
                )

            # 目标点已锁定,每帧幂等重发同一个 LAND(#543 纪律:别每帧重选目标点)。
            self.facade.cast_unit_ability(
                tag,
                land_ability_name,
                target={"kind": "point", "point": [float(land_point.x), float(land_point.y)]},
            )
            self._set_override_status(
                directive, "active", _i18n_t("structmove.landing", self._lang)
            )

        for d_id in done_ids:
            self._structure_move_orders.pop(d_id, None)

    # ------------------------------------------------------------------
    # BC 群骚扰（2026-06-29 #580 重构自 _bc_auto_harass 工厂）
    # ------------------------------------------------------------------
    # 2026-06-29 #580 group_harass 幂等更新 + partial-release
    # ------------------------------------------------------------------

    def _try_upsert_group_harass(self, submitted: Directive, now: float) -> bool:
        """group_harass claim 幂等更新：若 standing_orders 已有同 verb claim，更新它返回 True；否则 False。

        更新内容：target_count + task.primary_action.target。
        target_count 降低时调 _partial_release_group 释放多余 BC。
        返回 True = 已更新现有 claim（调用方跳过新增）；False = 无现有 claim（调用方正常追加）。
        """
        from vibecraft.directives.task import Verb

        payload = submitted.payload
        if not isinstance(payload, UnitClaimPayload):
            return False
        if payload.task.primary_action.verb != Verb.GROUP_HARASS:
            return False

        existing = next(
            (
                d
                for d in self.standing_orders
                if isinstance(d.payload, UnitClaimPayload)
                and d.payload.task.primary_action.verb == Verb.GROUP_HARASS
            ),
            None,
        )
        if existing is None:
            return False

        old_tc = existing.payload.target_count
        new_tc = payload.target_count

        # 更新 target_count + target（in-place；pydantic v2 非 frozen 可直接赋值）
        existing.payload.target_count = new_tc
        existing.payload.task.primary_action.target = payload.task.primary_action.target

        # partial-release：当前群成员 > 新 cap 时立即释放多余 BC
        current_count = len(self._standing_order_tags.get(existing.id, set()))
        if new_tc is not None and current_count > new_tc:
            self._partial_release_group(existing.id, current_count - new_tc)

        logger.info(
            "BCHARASSTRACE group_harass upsert did=%s old_tc=%s new_tc=%s current=%d",
            existing.id[:8],
            old_tc,
            new_tc,
            current_count,
        )
        return True

    def _partial_release_group(self, did: str, n: int) -> None:
        """group_harass claim 部分释放：从 _standing_order_tags[did] 移走 n 个 BC。

        释放策略（opus D2）：
        - 优先释放 health_percentage 最高的（满血 = 在家待命；残血 = 前线挨打不宜送菜）。
        - 从 recruit watcher seen 移除 → 支持后续 target_count 调高时重新入伍。
        - release_unit_role 归还 sharpy free_units；按 WP-C 尝试恢复给 prior 指令。
        - 清 _unit_semantics / _displaced 残留。
        """
        tags = set(self._standing_order_tags.get(did, set()))
        if not tags or n <= 0:
            return

        def _hp(tag: int) -> float:
            hp: float | None = None
            if hasattr(self.facade, "get_unit_health_percentage"):
                hp = self.facade.get_unit_health_percentage(tag)
            return hp if hp is not None else 1.0  # unknown → 视为满血

        # 降序：health 最高的先释放（满血在家优先）
        sorted_tags = sorted(tags, key=_hp, reverse=True)
        to_release = sorted_tags[:n]

        displaced = self._displaced.get(did, {})
        watcher = self._recruit_watchers.get(did)

        for tag in to_release:
            self._standing_order_tags[did].discard(tag)
            self._unit_semantics.pop(tag, None)
            # 清 seen：支持稍后重新入伍（target_count 调高时此 BC 重新被征）
            if watcher is not None:
                watcher["seen"].discard(tag)
            # WP-C 恢复逻辑：尝试还给 prior 指令；否则交回 bot
            prior = displaced.pop(tag, None)
            if prior is not None and self._restore_unit_to_prior(tag, prior):
                continue  # case 1/4: 恢复给 prior 或单位死了静默跳过
            # case 2/3: prior=None 或 prior 已结束
            if hasattr(self.facade, "release_unit_role"):
                self.facade.release_unit_role(tag)

        logger.info(
            "BCHARASSTRACE partial_release did=%s released=%d tags=%s",
            did[:8],
            len(to_release),
            sorted(to_release),
        )

    # ------------------------------------------------------------------

    def _tick_bc_group_harass(self, now: float) -> None:
        """每 tick:
        1. bc_rush 开局自动提交一条 group_harass unit_claim（只一次；玩家 ❌ 后不再重建）。
        2. 发布 bc_harass_groups list → knowledge.vibecraft.bc_harass_groups（GroupHarassAct 读）。
        """
        from vibecraft.directives.scope import Selector
        from vibecraft.directives.task import Action, Task, Verb
        from vibecraft.directives.types import IssuedBy

        # bc_rush 开局：自动提交一条 group_harass claim（只一次）。玩家 ❌ 后 flag 仍 True → 不重建。
        # getattr 兜底：单测可能用 __new__ 跳过 __init__ 构造 Director（没设这个 flag）。
        if (
            not getattr(self, "_bc_harass_group_auto_created", False)
            and (getattr(self._bot, "active_recipe", "") or "") == "bc_rush"
        ):
            self._bc_harass_group_auto_created = True
            auto_claim = Directive(
                payload=UnitClaimPayload(
                    selector=Selector(unit_type="BattleCruiser"),
                    task=Task(
                        primary_action=Action(
                            verb=Verb.GROUP_HARASS,
                            target=None,  # auto picker → GroupHarassAct 决策
                        )
                    ),
                    persistent=True,
                    recruit_new=True,
                    target_count=None,  # 无上限，所有 BC 都进群
                ),
                issued_at=now,
                issued_by=IssuedBy.BOT_INTERNAL,
                source_text="bc_rush: auto group_harass claim",
            )
            with contextlib.suppress(Exception):
                self._submit_directives([auto_claim], now)
                logger.info(
                    "BCHARASSTRACE group_claim_auto_created directive_id=%s", auto_claim.id[:8]
                )

        # 每 tick 重建并发布 bc_harass_groups
        self._publish_bc_harass_groups()

    def _publish_bc_harass_groups(self) -> None:
        """扫 standing_orders 里所有 group_harass claim，发布群信息到 knowledge.vibecraft.bc_harass_groups。

        GroupHarassAct 读此 list 取得群 tag 集 + 目标矿 + target_count。
        每 tick 重建；空列表也写（act 据此清场）。
        """
        from vibecraft.directives.scope import TargetKind
        from vibecraft.directives.task import Verb

        groups: list[dict] = []
        for d in self.standing_orders:
            payload = d.payload
            if not isinstance(payload, UnitClaimPayload):
                continue
            if payload.task.primary_action.verb != Verb.GROUP_HARASS:
                continue
            tags: set[int] = set(self._standing_order_tags.get(d.id, set()))
            action_target = payload.task.primary_action.target
            target_str: str | None = None
            if (
                action_target is not None
                and getattr(action_target, "kind", None) == TargetKind.NAMED_SPOT
            ):
                target_str = getattr(action_target, "named_spot", None)
            groups.append(
                {
                    "did": d.id,
                    "tags": tags,
                    "target": target_str,
                    "target_count": payload.target_count,
                }
            )

        vib = getattr(getattr(self._bot, "knowledge", None), "vibecraft", None)
        if vib is not None:
            vib.bc_harass_groups = groups

    # ------------------------------------------------------------------
    # harass_workers player claim 微操
    # ------------------------------------------------------------------

    def _tick_worker_harass(self) -> None:
        """每 tick：发布 worker_harass_tags，再驱动被 claim 单位执行 hit-and-run 微操。"""
        self._publish_worker_harass_tags()
        self._execute_worker_harass_micro()

    def _publish_worker_harass_tags(self) -> None:
        """扫 standing_orders 里所有 verb==HARASS_WORKERS 的 claim，汇总 tags 发布到 knowledge.vibecraft.worker_harass_tags。"""
        from vibecraft.directives.task import Verb

        tags: set[int] = set()
        for d in self.standing_orders:
            payload = d.payload
            if not isinstance(payload, UnitClaimPayload):
                continue
            if payload.task.primary_action.verb != Verb.HARASS_WORKERS:
                continue
            tags.update(self._standing_order_tags.get(d.id, set()))

        vib = getattr(getattr(self._bot, "knowledge", None), "vibecraft", None)
        if vib is not None:
            vib.worker_harass_tags = tags

    def _execute_worker_harass_micro(self) -> None:
        """对 worker_harass_tags 里每个单位运行 hit-and-run 打农民微操。"""
        from sc2.ids.unit_typeid import UnitTypeId as _UTI

        from vibecraft.bot.auto_combat.harass_act import player_harass_micro

        vib = getattr(getattr(self._bot, "knowledge", None), "vibecraft", None)
        if vib is None:
            return
        tags: set[int] = getattr(vib, "worker_harass_tags", set())
        if not tags:
            return

        bot = self._bot
        _WORKER_TYPES_D = frozenset({_UTI.PROBE, _UTI.SCV, _UTI.DRONE})
        _STATIC_DEF_D = frozenset(
            {
                _UTI.PHOTONCANNON,
                _UTI.SPINECRAWLER,
                _UTI.SPORECRAWLER,
                _UTI.MISSILETURRET,
                _UTI.BUNKER,
                _UTI.PLANETARYFORTRESS,
            }
        )

        try:
            enemy_workers = bot.enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES_D)
        except Exception:
            enemy_workers = None

        try:
            threats: list[Any] = []
            threats.extend(
                bot.enemy_units.filter(
                    lambda u: u.type_id not in _WORKER_TYPES_D and not u.is_structure
                )
            )
            threats.extend(bot.enemy_structures.filter(lambda s: s.type_id in _STATIC_DEF_D))
        except Exception:
            threats = []

        try:
            enemy_main = bot.enemy_start_locations[0]
        except Exception:
            enemy_main = None

        try:
            start_location = bot.start_location
        except Exception:
            start_location = None

        import os as _os_wh

        _trace_wh = bool(_os_wh.environ.get("VIBECRAFT_WHARASS_TRACE"))

        for tag in list(tags):
            try:
                unit = bot.units.by_tag(tag)
                if unit is None or not unit.is_ready:
                    continue
                # 真局自验 trace(env 门控)：记被 claim 单位到敌主基距离(验"死神真的到了敌矿",
                # 不只是发命令)。终态铁律靠此 dist 变小 + telemetry enemy_workers_harassed 上升。
                if _trace_wh and enemy_main is not None:
                    with contextlib.suppress(Exception):
                        logger.info(
                            "WHARASSTRACE pos tag=%d dist=%.1f hp=%.2f",
                            tag,
                            float(unit.distance_to(enemy_main)),
                            (float(unit.health) + float(unit.shield))
                            / max(1.0, float(unit.health_max) + float(unit.shield_max)),
                        )
                # 修复(根因 F80/F81):骚扰参考点 = **离本单位最近的敌方农民所在处**,
                # 不是死主基地。原来恒传 enemy_start_locations[0] → player_harass_micro 的
                # far 判定用「距主基 > 22」→ 骚扰离主基较远的**二矿**时 far 恒 True,飞龙
                # (射程仅 3)一直往主基方向飞、够不着二矿农民。改成每个骚扰单位各自扑向
                # 离自己最近的农民群(自然覆盖二矿),到位后再 hit-and-run;无可见农民时
                # fallback 回主基地(保留原「没视野直奔主基找」行为)。
                ref_point = enemy_main
                if enemy_workers:
                    with contextlib.suppress(Exception):
                        nearest_w = enemy_workers.closest_to(unit)
                        if nearest_w is not None:
                            ref_point = nearest_w.position
                player_harass_micro(
                    unit,
                    enemy_workers,
                    ref_point,
                    threats,
                    self._worker_harass_bailing,
                    start_location,
                )
            except Exception as exc:
                logger.debug("worker_harass_micro tag=%d fail: %s", tag, exc)

    def _maybe_pullback_clear(self, action: Any, now: float) -> None:
        """若该 action 是"回家/撤退"(standby → 己方主基地 named_spot),清过期全局 attack。

        persistent claim 走 _assign_standing_order_units、ephemeral 走 _apply_unit_claim,
        两条路都调本 helper —— 否则只挂 ephemeral 那条会漏掉真实的 persistent"回家防守"
        (2026-06-06 真局自测发现:intent 一路 attack 没清的根因)。
        """
        try:
            if action is None or action.verb.value != "standby":
                return
            spot = (getattr(action.target, "named_spot", None) or "").strip().lower()
        except Exception:
            return
        if spot in _HOME_NAMED_SPOTS:
            self._clear_global_attack_on_pullback(now)

    def _clear_global_attack_on_pullback(self, now: float) -> None:
        """玩家把主力'回家防守/撤退回家'(standby→己方主基地)时,清掉过期的全局
        attack 意图。复用 revoke_tactical = 等价自动按掉那张'强制全体进攻'卡片。

        仅当当前 active 的全局战术确实是 attack 才清;defend/retreat/hold 不动。
        理由:全局 attack 持续强攻没编队的 free_units(尤其新造单位)往前,玩家拉部队
        回家时这股力量还在撕扯 → 部队脱节 + 跳舞(2026-06-06 虚空 dancing bug)。
        """
        gid = self._current_l2_global_id
        if gid is None:
            return
        if self._tactical_overrides.get(gid) != "attack":
            return
        logger.info("回家/撤退 standby → 清过期全局 attack 意图 (global_id=%s)", gid[:8])
        self.revoke_tactical(gid, now)

    def _apply_unit_claim(self, d: Directive, payload: UnitClaimPayload, now: float) -> None:
        action = payload.task.primary_action
        verb_str = action.verb.value
        # vibecraft: 2026-06-06 "回家防守/撤退回家"= standby 到己方主基地 named_spot →
        # 顺手清掉过期的全局'强制全体进攻'意图(ephemeral 路径;persistent 在
        # _assign_standing_order_units 里同样调 _maybe_pullback_clear)。
        self._maybe_pullback_clear(action, now)
        # 2026-05-25 用户:cast_ability 特殊路径 — 玩家"给两个BF星空加速" →
        # nexus cast chrono boost on Forge。target 是 structure_type,不走标准
        # execute_unit_action(它只懂 move/attack point)。
        ability_id = action.ability_id or ""
        if verb_str == "cast_ability" and "chrono" in ability_id.lower():
            target = action.target
            structure_type = getattr(target, "structure_type", None) or getattr(
                target, "unit_type", None
            )
            count = payload.selector.count or 1
            if structure_type:
                cast_fn = getattr(self.facade, "cast_chrono_boost_on_structure", None)
                if cast_fn is not None:
                    try:
                        n = cast_fn(structure_type, count)
                        logger.info(
                            "chrono_boost: cast %d/%d on %s (id=%s)",
                            n,
                            count,
                            structure_type,
                            d.id[:8],
                        )
                    except Exception as exc:
                        logger.warning("chrono_boost cast fail: %s", exc)
                        self._set_override_status(d, "on_hold", f"chrono fail: {exc}")
                        return
                else:
                    logger.warning("chrono_boost: facade lacks method (id=%s)", d.id[:8])
            else:
                logger.warning("chrono_boost: target lacks structure_type")
            # cast_ability 是一次性命令,执行后立即关单
            self._release_directive_done(d, now, reason="chrono_boost_cast")
            return

        if verb_str == "cast_ability" and ability_id:
            # 2026-05-30:通用 cast_ability 路径（MORPH_ARCHON / PSISTORM / FEEDBACK 等）
            # chrono 已在上面特判，这里处理所有其他 ability。
            target = action.target
            unit_type = getattr(target, "unit_type", None) or payload.selector.unit_type
            count = payload.selector.count  # None = 全部
            # 对点施放的 ability（如 EFFECT_TACTICALJUMP 大舰传送回家）：target 是点/named_spot →
            # 解析成落点坐标传给 facade；自施放(archon/storm)解析不出点 → None。
            target_point: tuple[float, float] | None = None
            if target is not None:
                with contextlib.suppress(Exception):
                    resolved = self._resolve_target_spec_point(target)
                    if resolved is not None:
                        target_point = (float(resolved[0]), float(resolved[1]))
            cast_fn = getattr(self.facade, "cast_ability_on_units", None)
            if cast_fn is not None:
                try:
                    n = cast_fn(
                        ability_id=ability_id,
                        unit_type=unit_type,
                        target_kind=getattr(target, "kind", "self"),
                        count=count,
                        target_point=target_point,
                    )
                    logger.info(
                        "cast_ability %s: cast %d times (id=%s)",
                        ability_id,
                        n,
                        d.id[:8],
                    )
                except Exception as exc:
                    logger.warning("cast_ability fail: %s", exc)
                    self._set_override_status(d, "on_hold", f"cast fail: {exc}")
                    return
            else:
                logger.warning("cast_ability: facade lacks method (id=%s)", d.id[:8])
            self._release_directive_done(d, now, reason="cast_ability_done")
            return

        # 2026-05-25 bug 4:走 helper 截断 sel.count,跟 _assign_standing_order_units
        # 对齐(persistent / ephemeral 路径不再 spec 漂移)。
        tags = self._resolve_selector_with_count(payload.selector)
        if not tags:
            # 解析不到匹配单位（如"守瞭望塔的追猎"此刻不存在）→ 卡片报"未找到匹配单位"，
            # 不再裸 for tag in None 崩掉整局（2026-06-03 用户报的崩溃根因）。
            logger.warning(
                "unit_claim 没找到匹配单位 (id=%s, selector=%s)", d.id[:8], payload.selector
            )
            self._set_override_status(d, "on_hold", _i18n_t("err.noMatchingUnit", self._lang))
            return
        # group_harass / harass_workers：微操由 director 每 tick 主动调度（tag 集驱动），
        # 不走一次性 execute_unit_action（且 target 可为 None → 下面 model_dump 会崩）。
        if verb_str in ("group_harass", "harass_workers"):
            for tag in tags:
                self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            return
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            # 立即下发首条 primary_action（reaction 留给 M1+）
            self.facade.execute_unit_action(
                unit_tag=tag,
                verb=verb_str,
                target=action.target.model_dump(mode="json") if action.target else None,
                ability_id=action.ability_id,
            )

    # ------------------------------------------------------------------
    # 2026-05-30 镜头跟随（view_follow）
    # ------------------------------------------------------------------

    def _apply_view_follow(self, d: Directive, payload: ViewFollowPayload, now: float) -> None:
        """VIEW_FOLLOW commit 后执行：根据 target_kind 调 follow_unit 或 move_camera。

        superseded 语义：同时只允许 1 条 active view_follow。新来一条时旧的自动
        release（reason='superseded_by_new_follow'）。
        持续跟随：directive 保留在 _in_flight 里，每次 on_tick 的
        _tick_view_follow 都重新计算目标 + 调 facade。
        """
        # 先 supersede 旧 view_follow（若有）
        if self._active_view_follow_id and self._active_view_follow_id != d.id:
            old_id = self._active_view_follow_id
            # 从 _in_flight 移除旧的
            self._in_flight.pop(old_id, None)
            self._committed_directives.pop(old_id, None)
            import contextlib

            with contextlib.suppress(Exception):
                self.board.revoke(old_id, now)
            self._log_directive(
                "released",
                d.model_copy(update={"id": old_id}),
                now,
                reason="superseded_by_new_follow",
            )
            logger.info("view_follow superseded old=%s by new=%s", old_id[:8], d.id[:8])
        self._active_view_follow_id = d.id
        # 新 follow 开始：清掉上一条的锁定 tag（lock-on-first-resolve 从头来）
        self._view_follow_locked_tag = None
        # 新 follow 开始：清掉聚团迟滞历史（从头追最强团，不带旧位置偏差）
        self._last_view_follow_center = None
        # 立即执行一次（后续 _tick_view_follow 每帧更新）。新 follow：朝向历史从头算。
        self._view_follow_prev_centroid = None
        self._update_view_follow_camera(payload)
        logger.info(
            "view_follow: start kind=%s (id=%s)",
            getattr(payload, "target_kind", "unit"),
            d.id[:8],
        )

    @staticmethod
    def _units_centroid(units: list[Any]) -> Any:
        """单位列表的原始质心 SimpleNamespace(x, y)。"""
        import types

        n = len(units)
        cx = sum(u.position.x for u in units) / n
        cy = sum(u.position.y for u in units) / n
        return types.SimpleNamespace(x=cx, y=cy)

    def _view_follow_squad_units(self) -> list[Any]:
        """第一个有存活单位的 tactical squad 的单位列表（FIFO；全死 → 空）。"""
        if not self._tactical_squads or self._bot is None:
            return []
        own = self._bot.units
        for squad in self._tactical_squads.values():
            live = own.tags_in(squad.unit_tags)
            if getattr(live, "amount", 0) > 0:
                return list(live)
        return []

    def _view_follow_units(self, payload: ViewFollowPayload) -> list[Any]:
        """按 target_kind 取被跟随的单位列表（统一供 compute_follow_focus）。

        - army：最强团聚团（迟滞防分兵横跳）的单位；同时更新 _last_view_follow_center。
        - squad：首个存活 squad 单位。
        - task：执行该 task 的单位。
        - unit（默认）：锁定的单 tag 单位。
        无存活单位 → 空列表（上层静默，玩家 × 解除）。
        """
        kind = getattr(payload, "target_kind", "unit")
        bot = self._bot
        if bot is None:
            return []
        try:
            if kind == "army":
                from vibecraft.bot.telemetry import strongest_cluster_units

                units = strongest_cluster_units(bot, prev_center=self._last_view_follow_center)
                if units:
                    self._last_view_follow_center = self._units_centroid(units)
                return units
            if kind == "squad":
                return self._view_follow_squad_units()
            if kind == "group":
                # 2026-06-08 玩家:"镜头跟随 N 队" → 跟该语音编队所有存活单位的质心。
                gid = getattr(payload, "group_id", None)
                tags = self._voice_groups.get(gid) if gid is not None else None
                if not tags:
                    return []
                return list(bot.units.tags_in(tags))
            if kind == "task":
                task = getattr(payload, "task", None)
                if not task:
                    return []
                tags = self._task_unit_tags(task)
                if not tags:
                    return []
                return list(bot.units.tags_in(tags))
            # unit（默认）
            tag = self._resolve_view_follow_tag(payload)
            if tag is None:
                return []
            u = bot.units.by_tag(tag)
            return [u] if u is not None else []
        except Exception as exc:
            logger.debug("_view_follow_units fail: %s", exc)
            return []

    def _legacy_view_follow_camera(self, payload: ViewFollowPayload) -> None:
        """无 bot 时退回路径：focus 三规则需 bot 的单位运动 / 敌情数据，没 bot 就用旧
        facade 直连逻辑（unit→follow_unit，army/squad/task→质心 move_camera）。
        """
        kind = getattr(payload, "target_kind", "unit")
        try:
            if kind == "army":
                center = self._compute_view_follow_center()
                if center is not None:
                    self.facade.move_camera((center.x, center.y))
            elif kind == "squad":
                point = self._resolve_squad_center()
                if point is not None:
                    self.facade.move_camera(point)
            elif kind == "task":
                task = getattr(payload, "task", None)
                resolved = self._resolve_task_follow(task) if task else None
                if resolved is not None:
                    rkind, value = resolved
                    if rkind == "unit":
                        self.facade.follow_unit(value)
                    else:
                        self.facade.move_camera(value)
            else:
                tag = self._resolve_view_follow_tag(payload)
                if tag is not None:
                    self.facade.follow_unit(tag)
        except Exception as exc:
            logger.debug("legacy view_follow fail: %s", exc)

    def _update_view_follow_camera(self, payload: ViewFollowPayload) -> None:
        """取被跟随单位 → compute_follow_focus（移动看前方 / 停止看本身 / 交战看双方团重心）
        → move_camera。同时记录原始质心给下次算移动朝向。无单位 → 静默。
        """
        if self._bot is None:
            # focus 三规则需 bot 数据；无 bot（极早期 / FakeFacade 测试）退回旧逻辑。
            self._legacy_view_follow_camera(payload)
            return

        from vibecraft.bot.telemetry import compute_follow_focus

        units = self._view_follow_units(payload)
        if not units:
            return
        focus = compute_follow_focus(
            self._bot,
            units,
            prev_centroid=self._view_follow_prev_centroid,
            forward_offset=self.config.view_follow_forward_offset,
        )
        if focus is None:
            return
        # 2026-06-13 用户:镜头切换生硬 → 不再直接瞬跳,只更新目标点,
        # 由 _glide_view_follow_camera 每 tick 渐进滑过去。
        self._view_follow_cam_target = (float(focus.x), float(focus.y))
        self._glide_view_follow_camera()
        self._view_follow_prev_centroid = self._units_centroid(units)

    def _glide_view_follow_camera(self) -> None:
        """每 tick 把镜头朝 _view_follow_cam_target 渐进滑动（lerp）。

        2026-06-13 用户：move_camera 是瞬跳，聚团切换时镜头生硬。改为每 tick
        朝目标点移动 GLIDE_ALPHA 比例（~0.7s 滑到位）；离目标 < SNAP 格不再发
        （防微抖）。读不到当前镜头位置（FakeFacade / 极早期）→ 直接跳到目标。
        """
        tgt = self._view_follow_cam_target
        if tgt is None:
            return
        try:
            cur = self.facade.get_camera_center()
        except Exception:
            cur = None
        try:
            if cur is None:
                self.facade.move_camera(tgt)
                return
            dx = tgt[0] - cur[0]
            dy = tgt[1] - cur[1]
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < self._VIEW_FOLLOW_GLIDE_SNAP:
                return
            a = self._VIEW_FOLLOW_GLIDE_ALPHA
            self.facade.move_camera((cur[0] + dx * a, cur[1] + dy * a))
        except Exception as exc:
            logger.debug("view_follow glide fail: %s", exc)

    def _apply_task_follow(self, payload: ViewFollowPayload, d: Directive) -> None:
        """target_kind="task" 的镜头动作（_apply_view_follow + _tick_view_follow 共用）。

        单个单位 → follow_unit（平滑原生跟随）；多个 → move_camera（质心）。
        任务无存活单位 → 静默跳过（玩家 × 解除）。
        """
        task = getattr(payload, "task", None)
        if not task:
            logger.warning("view_follow: target_kind=task 缺 task 字段 (id=%s)", d.id[:8])
            return
        resolved = self._resolve_task_follow(task)
        if resolved is None:
            logger.debug("view_follow: task=%s 无存活单位 (id=%s)", task, d.id[:8])
            return
        kind, value = resolved
        try:
            if kind == "unit":
                self.facade.follow_unit(value)
            else:
                self.facade.move_camera(value)
        except Exception as exc:
            logger.debug("view_follow task follow fail: %s", exc)

    def _resolve_view_follow_tag(self, payload: ViewFollowPayload) -> int | None:
        """解析 view_follow payload → 当前最近存活单位 tag。

        优先 payload.unit_tag（玩家显式指定的精确 tag）；
        否则按 unit_type resolve，并 **锁定首次结果**（_view_follow_locked_tag）：
        后续 tick 一直跟同一单位，直到它死才重新 resolve。不锁会每 tick 取
        resolve_selector(unit_type)[0]，集合顺序变 → 镜头在同型单位间跳。
        """
        if payload.unit_tag is not None:
            # 玩家显式 tag：先确认单位还活着
            alive = self.facade.resolve_selector(tag=payload.unit_tag)
            if alive:
                return payload.unit_tag
        if payload.unit_type:
            # 已锁定且仍存活 → 继续跟它（防跳）
            if self._view_follow_locked_tag is not None:
                if self.facade.resolve_selector(tag=self._view_follow_locked_tag):
                    return self._view_follow_locked_tag
                self._view_follow_locked_tag = None  # 锁定单位已死，重新 resolve
            tags = self.facade.resolve_selector(unit_type=payload.unit_type)
            if tags:
                self._view_follow_locked_tag = tags[0]
                return tags[0]
        return None

    def _resolve_squad_center(self) -> tuple[float, float] | None:
        """取第一个有存活单位的 tactical squad 的中心坐标。

        squad 按 _tactical_squads 的插入顺序遍历（FIFO），找到第一个 unit 存活
        的 squad 算其质心并返回 (x, y) tuple。
        squad 全死 → 返回 None（不调 move_camera，防异常回归）。
        """
        if not self._tactical_squads:
            return None
        if self._bot is None:
            return None
        try:
            own_units = self._bot.units
            for squad in self._tactical_squads.values():
                live_units = own_units.tags_in(squad.unit_tags)
                if live_units.amount > 0:
                    cx = sum(u.position.x for u in live_units) / live_units.amount
                    cy = sum(u.position.y for u in live_units) / live_units.amount
                    return (cx, cy)
        except Exception as exc:
            logger.debug("_resolve_squad_center fail: %s", exc)
        return None

    # task → 正在执行该任务的单位 tag 来源（2026-06-01 target_kind="task"）。
    # 从已有结构读，不新增 tag 跟踪：
    #   scout       → bot.scout_worker.scout_tag（内建自动探路，t≥60 才激活）
    #                 + verb=scout 的 squad + SCOUT directive 的 _standing_order_tags
    #                 （玩家"派农民探路"，t<60 内建探路兵未激活时唯一来源）
    #   harass      → _tactical_squads 里 verb ∈ {harass, recon} 的 squad
    #   patrol      → unit_claim 的 primary_action.verb == patrol 的 _standing_order_tags
    #   watchtower  → unit_claim 的 verb ∈ {guard_position, standby, hold_position}
    _TASK_SQUAD_VERBS: ClassVar[dict[str, frozenset[str]]] = {
        "scout": frozenset({"scout"}),
        "harass": frozenset({"harass", "recon"}),
    }
    _TASK_CLAIM_VERBS: ClassVar[dict[str, frozenset[str]]] = {
        "patrol": frozenset({"patrol"}),
        "watchtower": frozenset({"guard_position", "standby", "hold_position"}),
    }

    def _task_unit_tags(self, task: str) -> set[int]:
        """收集正在执行 task 的单位 tag（按 task 从对应数据源读）。"""
        tags: set[int] = set()
        # scout：内建 ScoutWorker 的当前侦察兵（精确单 tag；t<60 时为 None）
        if task == "scout":
            sw = getattr(self._bot, "scout_worker", None)
            st = getattr(sw, "scout_tag", None)
            if isinstance(st, int) and st:
                tags.add(st)
        # squad 来源（harass/recon squad；recon-style scout squad）
        squad_verbs = self._TASK_SQUAD_VERBS.get(task)
        if squad_verbs:
            for sq in self._tactical_squads.values():
                if sq.verb in squad_verbs:
                    tags |= sq.unit_tags
        # directive-claim 来源：扫 _standing_order_tags（被指令单位的 reserve 表），
        # 按 directive 类型 / verb 匹配 task。涵盖 ephemeral SCOUT directive
        # （玩家"派农民探路"）+ persistent unit_claim（patrol / watchtower）。
        for did, dtags in self._standing_order_tags.items():
            if not dtags:
                continue
            d = self._lookup_active_directive(did)
            if d is not None and self._directive_matches_task(d, task):
                tags |= dtags
        return tags

    def _lookup_active_directive(self, directive_id: str) -> Directive | None:
        """按 id 在 in_flight / committed / standing_orders 里找 directive（兜底 None）。"""
        d = self._in_flight.get(directive_id) or self._committed_directives.get(directive_id)
        if d is not None:
            return d
        for s in self.standing_orders:
            if s.id == directive_id:
                return s
        return None

    def _directive_matches_task(self, directive: Directive, task: str) -> bool:
        """directive 是否属于 task：scout→SCOUT 类型；patrol/watchtower→unit_claim + verb 匹配。"""
        if task == "scout":
            return directive.type == DirectiveType.SCOUT
        claim_verbs = self._TASK_CLAIM_VERBS.get(task)
        if claim_verbs and directive.type == DirectiveType.UNIT_CLAIM:
            return self._standing_order_action_verb(directive) in claim_verbs
        return False

    @staticmethod
    def _standing_order_action_verb(directive: Directive) -> str | None:
        """取 unit_claim 的 primary_action.verb 字符串值（兜底 None）。"""
        try:
            verb = directive.payload.task.primary_action.verb  # type: ignore[union-attr]
            return str(getattr(verb, "value", verb))
        except Exception:
            return None

    def _resolve_task_follow(self, task: str) -> tuple[str, Any] | None:
        """解析 target_kind="task" → 镜头动作。

        返回 ("unit", tag)（单个 → follow_unit 平滑跟）或
        ("camera", (x, y))（多个 → move_camera 跟质心）或 None（无存活单位）。
        """
        if self._bot is None:
            return None
        tags = self._task_unit_tags(task)
        if not tags:
            return None
        try:
            units = self._bot.units.tags_in(tags)
            n = getattr(units, "amount", None)
            if n is None:
                units = list(units)
                n = len(units)
            if n == 0:
                return None
            if n == 1:
                only = next(iter(units))
                return ("unit", only.tag)
            cx = sum(u.position.x for u in units) / n
            cy = sum(u.position.y for u in units) / n
            return ("camera", (cx, cy))
        except Exception as exc:
            logger.debug("_resolve_task_follow(%s) fail: %s", task, exc)
            return None

    def _tick_view_follow(self, now: float) -> None:
        """每 tick 调用：若有 active view_follow，按 target_kind 更新镜头。

        - target_kind="unit": 重新 resolve 最近单位 + follow_unit
            单位死亡（resolve 返空）→ 静默等待（玩家可 × 解除）
            unit_tag 精确锁 → 始终跟该 tag；死后静默。
        - target_kind="army": 重算主力质心 + move_camera
            无单位时静默跳过。
        - target_kind="squad": 重算 squad 质心 + move_camera
            squad 全死 → 静默跳过（不清 active id，玩家 × 解除）。

        view_follow commit 后在 _committed_directives（不在 _in_flight），
        所以先查 _in_flight，再 fallback _committed_directives。
        """
        did = self._active_view_follow_id
        if did is None:
            self._view_follow_cam_target = None
            return
        # 2026-06-13 用户:焦点重算分频降一半(1/16);镜头本身每 tick 朝目标渐进滑动。
        self._view_follow_tick_count += 1
        if self._view_follow_tick_count % self._VIEW_FOLLOW_REFRESH_DIV == 0:
            # commit 后 directive 在 _committed_directives（_dispatch_committed_to_facade pop 了）
            d = self._in_flight.get(did) or self._committed_directives.get(did)
            if d is None:
                # 已被 revoke / grace 期过期删掉，清 active id
                self._active_view_follow_id = None
                self._view_follow_cam_target = None
                return
            if isinstance(d.payload, ViewFollowPayload):
                self._update_view_follow_camera(d.payload)
                return  # _update 内已 glide 一次
        self._glide_view_follow_camera()

    # ------------------------------------------------------------------
    # 2026-05-30 产能封锁（production_block）
    # ------------------------------------------------------------------

    def _apply_production_block(
        self, d: Directive, payload: ProductionBlockPayload, now: float
    ) -> None:
        """PRODUCTION_BLOCK commit 后执行：把 unit_type 加入 facade production_blocked set。

        同一兵种不重复封锁（幂等）。directive 持续在 _in_flight，
        revoke_directive 调 _apply_production_block_revoke 解除。
        """
        unit_type = payload.unit_type
        self._production_blocks[d.id] = unit_type
        block_fn = getattr(self.facade, "block_production", None)
        if block_fn is not None:
            try:
                block_fn(unit_type)
                logger.info("production_block: block %s (id=%s)", unit_type, d.id[:8])
            except Exception as exc:
                logger.warning("production_block block_production fail: %s", exc)
        else:
            logger.warning(
                "production_block: facade lacks block_production method (id=%s)", d.id[:8]
            )
        self._set_override_status(
            d, "active", _i18n_t("block.active", self._lang, unit_type=unit_type)
        )

    def _apply_production_block_revoke(self, directive_id: str, now: float) -> bool:
        """玩家 × 或 revoke_directive 解除产能封锁。

        从 facade production_blocked set 移除对应 unit_type，
        从 _production_blocks dict 移除 directive_id，
        从 _in_flight 移除 directive。
        返回 True 表示找到并解除；False 表示没找到（不是 production_block type）。
        """
        unit_type = self._production_blocks.pop(directive_id, None)
        if unit_type is None:
            return False
        unblock_fn = getattr(self.facade, "unblock_production", None)
        if unblock_fn is not None:
            try:
                unblock_fn(unit_type)
                logger.info(
                    "production_block revoked: unblock %s (id=%s)",
                    unit_type,
                    directive_id[:8],
                )
            except Exception as exc:
                logger.warning("production_block unblock_production fail: %s", exc)
        else:
            logger.warning(
                "production_block: facade lacks unblock_production (id=%s)", directive_id[:8]
            )
        # 清 _in_flight + _committed + board
        self._in_flight.pop(directive_id, None)
        self._committed_directives.pop(directive_id, None)
        import contextlib

        with contextlib.suppress(Exception):
            self.board.revoke(directive_id, now)
        self._override_status.pop(directive_id, None)
        self._push_event(
            {
                "type": "event",
                "kind": "directive.revoked",
                "ts": now,
                "payload": {"directive_id": directive_id, "reason": "player_x"},
            }
        )
        self._push_snapshot(now)
        return True

    def _apply_unit_release(self, payload: UnitReleasePayload) -> None:
        sel = payload.selector
        # 2026-05-25 bug 4:走 helper 截断 sel.count(释放"一个农民"也只释放 1 个)。
        tags = self._resolve_selector_with_count(sel)
        if not tags and sel.claimed is True:
            # 释放所有已 claim(claimed=True 语义就是批量释放,不按 count)
            tags = list(self.board.unit_claims.keys())
        target_role = UnitRole.IDLE if payload.return_to_role == "IDLE" else UnitRole.ARMY
        for tag in tags:
            self.facade.set_unit_role(tag, target_role)
        # 规则3(2026-06-08 用户):释放单位连带撤销控制它们的其它指令(撤退/进攻/待命卡),
        # 彻底还给 bot。否则"释放所有虚空"后,虚空身上的撤退指令还在(玩家报)。
        self._cancel_directives_controlling_units(tags, float(getattr(self._bot, "time", 0.0)))
        # Task #352: 玩家"让探路农民回来" → 若 selector 是 worker 类型,
        # 取消 ScoutWorker（否则下一 tick 它会重新 reserve 同一个农民继续探路）。
        _WORKER_TYPES = {"Probe", "SCV", "Drone"}
        if sel.unit_type in _WORKER_TYPES or (sel.unit_type is None and sel.claimed is True):
            scout_worker = getattr(self._bot, "scout_worker", None)
            if scout_worker is not None and not getattr(scout_worker, "cancelled", False):
                try:
                    scout_worker.cancel()
                except Exception as exc:
                    logger.debug("ScoutWorker cancel fail: %s", exc)

    # ------------------------------------------------------------------
    # L2 tactical_objective executor（P0b Task 12）
    # ------------------------------------------------------------------

    def _exec_tactical_objective(self, d: Directive, payload: TacticalObjectivePayload) -> None:
        """L2 分流入口：A 类（override flag）/ B 类（squad 抢占）/ 其他（on_hold）。

        2026-05-24 用户:vision = "盯着 X" 应派 1 单位 hold,不是设大部队攻击点。
        改走 squad 路径(类 SCOUT)。done_when=vision_acquired hold_seconds 长。
        unit_count_hint/unit_type_hint 缺省时默认 1 Probe。
        """
        verb = payload.verb
        if verb == "vision":
            # 默认 1 Probe 派去(LLM 可覆盖 hint)。
            if payload.unit_count_hint is None or not payload.unit_type_hint:
                # 复制 payload 注入默认 hint
                patched = payload.model_copy(
                    update={
                        "unit_count_hint": payload.unit_count_hint or 1,
                        "unit_type_hint": payload.unit_type_hint or ["Probe"],
                    }
                )
                self._exec_l2_squad(d, patched)
            else:
                self._exec_l2_squad(d, payload)
            return
        if verb in _A_VERBS:
            self._exec_l2_global(d, payload)
        elif verb in _B_VERBS:
            self._exec_l2_squad(d, payload)
        else:
            logger.warning("L2 verb %r MVP 未支持 (id=%s)", verb, d.id[:8])
            self._set_override_status(
                d, "on_hold", _i18n_t("err.verbUnsupported", self._lang, verb=verb)
            )

    def _exec_l2_global(self, d: Directive, payload: TacticalObjectivePayload) -> None:
        """A 类：attack/defend/retreat/vision → facade override flag。

        persistent=True（原 engagement_constraint 语义）时额外写 stance_override，
        让 PlanZoneAttack 在本次 attack 结束后也继续保持该姿态（持续生效）。
        persistent=False（默认）= 一次性，本次 attack 完成后 bot 恢复自由决策。
        """
        # 清前一条 active L2 global；把旧 directive 标 done（被新指令覆盖）
        old_id_for_event = None
        old_verb_for_event = None
        if self._current_l2_global_id and self._current_l2_global_id != d.id:
            old_id = self._current_l2_global_id
            old_id_for_event = old_id
            old_verb_for_event = self._tactical_overrides.get(old_id)
            self._tactical_overrides.pop(old_id, None)
            old_d = self._current_l2_global_directive
            if old_d is not None and old_d.id == old_id:
                self._set_override_status(
                    old_d, "done", _i18n_t("directive.superseded", self._lang)
                )
                # 2026-05-27 用户:覆盖的卡片要在几秒后真消失(不只是变灰)。
                # 进 _done_at,on_tick grace expired 后清掉 PWA 卡片。
                # 修前:status=done 但 _done_at 没设 → 卡片永远灰着。
                self._done_at[old_id] = float(getattr(self._bot, "time", 0.0))
        point = self._resolve_target_area(payload.target_area)
        try:
            self.facade.set_attack_target_override(point)
            self.facade.set_combat_intent_override(payload.verb)  # type: ignore[arg-type]
            # P1b: persistent=True 同时写 stance_override（持续生效，旧 engagement_constraint 语义）
            # 2026-05-28 用户:hold 默认 persistent=True(坚守持续到 × 解除)
            if payload.persistent and payload.verb in ("defend", "retreat", "hold"):
                self.facade.set_engagement_stance(payload.verb)
            # 2026-05-25 bug B 修复:切 verb 时清 attack_mode_override 残留
            # 否则玩家"强制全体进攻"(all_in)后切"全军撤退",mode 仍是 all_in
            # → PlanZoneAttack._should_retreat 看 mode=all_in → NotActive → 不撤
            # attack verb 的 mode 由 _submit_tactical_action 在 submit 前 set,
            # 这里跳过 attack 避免覆盖
            if payload.verb != "attack":
                set_mode = getattr(self.facade, "set_attack_mode_override", None)
                if set_mode is not None:
                    set_mode(None)
            # 2026-05-28 用户 hold:聚团到 target 或 current army_center 锁住。
            # 切其他 verb 时清 hold_gather_point 防 PlanZoneGather 读到旧点。
            set_hgp = getattr(self.facade, "set_hold_gather_point", None)
            if set_hgp is not None:
                if payload.verb == "hold":
                    hgp = point if point is not None else self._compute_current_army_center()
                    set_hgp(hgp)
                elif payload.verb == "defend":
                    # vibecraft: 2026-06-03 用户 — defend 也走 hold_gather_point。
                    # 有目标(瞭望塔/分矿)→ 守该点;无目标→None,vendor zone_gather
                    # 自己挑离敌最近的己方分矿(需 zone 数据,Director 这层拿不到)。
                    set_hgp(point)
                else:
                    set_hgp(None)
            # 2026-05-28 用户 probe/recon 聚团门:attack mode=probe 时启 timer
            # (15s 内 _should_attack 卡聚团等);切其他战术清 timer
            set_regroup = getattr(self.facade, "set_regroup_started", None)
            if set_regroup is not None:
                if payload.verb == "attack" and getattr(payload, "attack_mode", None) == "probe":
                    set_regroup(float(getattr(self._bot, "time", 0.0)))
                else:
                    set_regroup(None)
        except Exception as exc:
            logger.debug("L2 global override fail: %s", exc)
            self._set_override_status(d, "on_hold", _i18n_t("err.facadeFail", self._lang, exc=exc))
            return
        self._tactical_overrides[d.id] = payload.verb
        self._current_l2_global_id = d.id
        self._current_l2_global_directive = d
        target_desc = payload.target_area or ""
        persistent_suffix = (
            _i18n_t("card.persistentSuffix", self._lang) if payload.persistent else ""
        )
        self._set_override_status(
            d, "active", f"{payload.verb} {target_desc}{persistent_suffix}".strip()
        )
        # 2026-05-28 诊断:每次 tactical 切换 emit event 进 events.jsonl,
        # 出"经常失效"问题时能离线回放完整轨迹(submit/commit/supersede/intent)。
        # warning level 让 stdout 也看到。
        target_resolved = point is not None
        try:
            now_t = float(getattr(self._bot, "time", 0.0))
        except Exception:
            now_t = 0.0
        self._push_event(
            {
                "type": "event",
                "kind": "tactical_change",
                "ts": now_t,
                "payload": {
                    "new_id": d.id,
                    "new_verb": payload.verb,
                    "new_mode": getattr(payload, "attack_mode", None),
                    "new_persistent": payload.persistent,
                    "target_area": payload.target_area,
                    "target_resolved": target_resolved,
                    "superseded_id": old_id_for_event,
                    "superseded_verb": old_verb_for_event,
                },
            }
        )
        logger.warning(  # 调试用,出问题时 stdout 立即可见
            "tactical_change: %s(%s) → %s(%s persistent=%s target=%s resolved=%s)",
            old_verb_for_event,
            (old_id_for_event or "")[:8] if old_id_for_event else "-",
            payload.verb,
            d.id[:8],
            payload.persistent,
            payload.target_area,
            target_resolved,
        )

    def _compute_current_army_center(self) -> Any:
        """算当前主力质心(排工人 / 非战斗支援 / 持久任务单位 / 建筑)。

        hold target_area=None 时用 — 玩家"原地坚守"的"原地"= 主力当前所在。
        触发时算一次锁住(写到 vibecraft.hold_gather_point),后续 PlanZoneGather
        读这个固定点,不每 tick 重算(防聚团点跟着兵走永远追不上)。

        排除口径与 telemetry army_center 共用 compute_army_center(role-based:harass/
        drop/proxy/巡逻/守瞭望塔/侦察 单位不算主力)。失败 / 早期没兵 → start_location 兜底。
        """
        from vibecraft.bot.telemetry import compute_army_center

        try:
            center = compute_army_center(self._bot)
            if center is not None:
                return center
        except Exception as exc:
            logger.debug("_compute_current_army_center fail: %s", exc)
        # 兜底:start_location(主基地)
        try:
            return self._bot.start_location
        except Exception:
            return None

    def _compute_view_follow_center(self) -> Any:
        """view_follow army 模式专用：聚团 + 造价加权 + 迟滞，返回最强团质心。

        与 _compute_current_army_center 区别：用 compute_strongest_cluster_center
        聚团选最强团，同时维护 _last_view_follow_center 做帧间迟滞（防镜头横跳）。
        失败 / None → start_location 兜底。
        """
        from vibecraft.bot.telemetry import compute_strongest_cluster_center

        try:
            center = compute_strongest_cluster_center(
                self._bot,
                prev_center=self._last_view_follow_center,
            )
            if center is not None:
                self._last_view_follow_center = center
                return center
        except Exception as exc:
            logger.debug("_compute_view_follow_center fail: %s", exc)
        # 兜底:start_location(主基地)
        try:
            return self._bot.start_location
        except Exception:
            return None

    # 2026-05-25 bug 10:UI button(recon/harass/scout)不传 unit_count/type_hint,
    # 之前 _exec_l2_squad 直接 on_hold 不派单位。按 verb 注入 sensible default。
    _B_VERB_DEFAULT_HINTS: ClassVar[dict[str, tuple[int, str]]] = {
        "recon": (4, "Stalker"),  # 中后期成建制小队试探
        "harass": (2, "Phoenix"),  # 早中期骚扰
        "scout": (1, "Probe"),  # 农民探路
    }

    def _exec_l2_squad(self, d: Directive, payload: TacticalObjectivePayload) -> None:
        """B 类：harass/scout/recon → 抢占 free unit → set_unit_role LLM_CONTROLLED。

        2026-05-25 bug 10:UI button verb 不带 hint 时按 _B_VERB_DEFAULT_HINTS
        注入 default,避免点 button 直接 on_hold(玩家看到卡片但单位不动)。
        LLM voice 路径如果带 hint 优先用 LLM 的。
        """
        count_hint = payload.unit_count_hint
        type_hint = payload.unit_type_hint[0] if payload.unit_type_hint else None
        if count_hint is None or not type_hint:
            defaults = self._B_VERB_DEFAULT_HINTS.get(payload.verb)
            if defaults is None:
                self._set_override_status(
                    d, "on_hold", _i18n_t("err.noDefaultHint", self._lang, verb=payload.verb)
                )
                return
            if count_hint is None:
                count_hint = defaults[0]
            if not type_hint:
                type_hint = defaults[1]
        n_wanted = count_hint
        unit_type = type_hint
        free_tags = self.facade.resolve_selector(unit_type=unit_type)
        tags = free_tags[:n_wanted]
        if not tags:
            self._set_override_status(
                d, "on_hold", _i18n_t("err.noFreeUnit", self._lang, unit=unit_type)
            )
            return
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
        # 2026-05-25 bug 10:target_area 缺时按 verb 给 default 区域,避免 squad 没目标
        target_area = payload.target_area
        if target_area is None and payload.verb in ("recon", "harass", "scout"):
            target_area = "enemy_natural"  # 默认前压敌方二矿(最常见侦查/骚扰目标)
        target_pt = self._resolve_target_area(target_area)
        # sharpy MoveType lazy import（防 e2e import 路径错误）
        try:
            from sharpy.combat.move_type import MoveType

            move_type: Any = MoveType.Harass if payload.verb == "harass" else MoveType.Assault
        except Exception:
            move_type = None
        # 2026-05-28 用户:recon 派遣时先聚团 — 算 squad center,target 暂改 center,
        # 让单位先回拢;execute_tactics_step 每 tick 检查 spread,< 8 grid 切到真 target
        initial_target = target_pt
        real_target = None
        regroup_started = None
        if payload.verb == "recon":
            try:
                squad_units = self._bot.units.tags_in(set(tags))
                if squad_units and squad_units.amount >= 2:
                    sx = sum(u.position.x for u in squad_units) / squad_units.amount
                    sy = sum(u.position.y for u in squad_units) / squad_units.amount
                    spread_ok = True
                    for u in squad_units:
                        if ((u.position.x - sx) ** 2 + (u.position.y - sy) ** 2) ** 0.5 > 8.0:
                            spread_ok = False
                            break
                    if not spread_ok:
                        from sc2.position import Point2

                        initial_target = Point2((sx, sy))
                        real_target = target_pt
                        regroup_started = float(getattr(self._bot, "time", 0.0))
            except Exception as exc:
                logger.debug("recon regroup precheck fail: %s", exc)
        squad = TacticalSquad(
            directive_id=d.id,
            unit_tags=set(tags),
            target=initial_target,
            move_type=move_type,
            verb=payload.verb,
            n_wanted=n_wanted,
            n_locked=len(tags),
            real_target=real_target,
            regroup_started_at=regroup_started,
            unit_type=unit_type,  # backfill 用
        )
        self._tactical_squads[d.id] = squad
        if len(tags) == n_wanted:
            msg = _i18n_t("ack.claimed", self._lang, n=len(tags), unit=unit_type)
        else:
            msg = _i18n_t("ack.claimedShort", self._lang, n=len(tags), nw=n_wanted, unit=unit_type)
        self._set_override_status(d, "active", msg)

    def _resolve_target_area(self, area: Any) -> Any:
        """area: str (named_spot) / (x,y) tuple / None → Point2 或 None。

        2026-05-28:走 NamedSpotRegistry 解析 70+ named spots
        (enemy_third / enemy_main_ramp / clock_X / own_clock_X / 方位 alias /
        watchtower / forward 等),不再只支持 4 个硬编码 spot。

        旧 alias 兼容(own_main → main, own_natural → natural):registry 不识别
        own_* 前缀,在这里手工 strip 一次。
        """
        if area is None:
            return None
        # Task C 防御：_inject_camera_point 已在 submit 前将 "camera" 替换成 tuple。
        # 若仍到此处（例外路径），返回 None 避免 NamedSpotRegistry 收到无效 key。
        if area == "camera":
            return None
        try:
            from sc2.position import Point2
        except Exception:
            return None
        if isinstance(area, (tuple, list)) and len(area) == 2:
            try:
                return Point2((float(area[0]), float(area[1])))
            except Exception:
                return None
        if self._bot is None:
            return None
        # alias normalize:own_main / own_natural / own_third → main / natural / third
        if isinstance(area, str):
            for alias_in, alias_out in (
                ("own_main", "main"),
                ("own_natural", "natural"),
                ("own_third", "third"),
            ):
                if area == alias_in:
                    area = alias_out
                    break
        try:
            from vibecraft.bot.named_spot import NamedSpotRegistry

            registry = NamedSpotRegistry()
            return registry.resolve(area, self._bot)
        except Exception as exc:
            logger.debug("_resolve_target_area(%s) failed: %s", area, exc)
            return None
        return None

    def _cached_combat_manager(self) -> Any:
        """缓存 sharpy combat_manager 引用（lazy lookup once）。

        真 sharpy 路径：knowledge.combat_manager（knowledge.py:59）。
        """
        if hasattr(self, "_cm_cache"):
            return self._cm_cache
        cm = None
        try:
            cm = self._bot.knowledge.combat_manager  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("combat_manager 不可用 (sharpy 接口不一致?): %s", exc)
        if cm is not None:
            self._cm_cache: Any = cm
        return cm

    async def execute_tactics_step(self, now: float) -> None:
        """每 sharpy step 调，给 active squad 派活（GroupCombatManager）。

        真 sharpy 签名：cm.add_units(units: Units)，然后 cm.execute(target, move_type)。
        add_units 每 tick 都要调（execute 内部会 clear _tags）。

        2026-05-28 用户:recon 加聚团门 + 撤退判定。
        - 聚团门:spread > 8 grid → target 暂用 squad center 拢回;< 8 切真 target;
          15s 超时强制 bypass(防卡死)
        - 撤退判定(占不到便宜就撤):敌方 visible 力量 × 1.2 >= 我方 squad 力量 → revoke
          (跟 _should_retreat 同语义,recon 系数更激进 = 1.2 比 probe 1.0 更宽松)
        """
        if not self._tactical_squads:
            return
        if self._bot is None:
            return
        cm = self._cached_combat_manager()
        if cm is None:
            return
        # 2026-05-28 用户:拉式征兵 backfill + 全死 → 自动终止(reason='units_lost')
        # 顺序:① 死亡剔除 → ② 缺人时去 free pool 抓符合类型的新单位补进 →
        # ③ 补完仍 0 unit → _release_directive_done(reason='units_lost')
        # squad 互斥:已被其他 squad 抓走的 tag 不能被本 squad 拉走(FIFO 先得)
        own_alive_tags = {u.tag for u in self._bot.units}
        for squad in list(self._tactical_squads.values()):
            try:
                squad_d = self._committed_directives.get(squad.directive_id) or self._in_flight.get(
                    squad.directive_id
                )
                # ① 死亡剔除:unit_tags 里已死的 tag 移除
                squad.unit_tags &= own_alive_tags
                # ② 缺人 backfill(squad 有 unit_type 且 已抓 < n_wanted)
                # 拉式征兵:每拍主动扫 free pool,抓符合类型的新单位补进来
                # squad 互斥:FIFO 先得,已被其他 squad 抓走的 tag 不能再拉
                if squad.unit_type is not None and len(squad.unit_tags) < squad.n_wanted:
                    taken_by_others: set[int] = set()
                    for other in self._tactical_squads.values():
                        if other.directive_id != squad.directive_id:
                            taken_by_others |= other.unit_tags
                    free_tags = self.facade.resolve_selector(unit_type=squad.unit_type)
                    need = squad.n_wanted - len(squad.unit_tags)
                    fresh = [
                        t
                        for t in free_tags
                        if t not in taken_by_others and t not in squad.unit_tags
                    ][:need]
                    if fresh:
                        for t in fresh:
                            self.facade.set_unit_role(t, UnitRole.LLM_CONTROLLED)
                        squad.unit_tags |= set(fresh)
                        squad.n_locked = len(squad.unit_tags)
                        if squad_d is not None:
                            short = (
                                _i18n_t("common.shortSuffix", self._lang)
                                if squad.n_locked < squad.n_wanted
                                else ""
                            )
                            self._set_override_status(
                                squad_d,
                                "active",
                                _i18n_t(
                                    "ack.claimedProgress",
                                    self._lang,
                                    n=squad.n_locked,
                                    nw=squad.n_wanted,
                                    unit=squad.unit_type,
                                    short=short,
                                ),
                            )
                # 重新拉一次 alive units(死亡剔除 + backfill 后)
                units = self._bot.units.tags_in(squad.unit_tags)
                if not units:
                    # ③ 全死 → _release_directive_done(reason='units_lost')
                    # 用户:全部死亡一瞬间就算,卡片转 done(暗红"单位全失")+ 2s grace
                    if squad_d is not None:
                        logger.info("squad %s 单位全失,auto_terminated", squad.directive_id[:8])
                        self._release_directive_done(squad_d, now, "units_lost")
                    self._tactical_squads.pop(squad.directive_id, None)
                    continue
                # recon 聚团状态机
                if squad.verb == "recon" and squad.real_target is not None:
                    elapsed = now - (squad.regroup_started_at or now)
                    if elapsed > 15.0:
                        # 超时:强制切真 target
                        squad.target = squad.real_target
                        squad.real_target = None
                        squad.regroup_started_at = None
                    else:
                        # 检查 spread:< 8 grid 切真 target
                        cx = sum(u.position.x for u in units) / units.amount
                        cy = sum(u.position.y for u in units) / units.amount
                        spread_ok = True
                        for u in units:
                            if ((u.position.x - cx) ** 2 + (u.position.y - cy) ** 2) ** 0.5 > 8.0:
                                spread_ok = False
                                break
                        if spread_ok:
                            squad.target = squad.real_target
                            squad.real_target = None
                            squad.regroup_started_at = None
                # recon 撤退判定:敌方 local power × 1.2 >= 我方 squad power → revoke
                # (占不到便宜就撤,系数 1.2 比 probe 1.0 更宽松 = 敌方稍弱也撤)
                if squad.verb == "recon" and self._should_recon_retreat(units, squad.target):
                    logger.warning(
                        "recon squad %s retreat: 敌方力量占优,revoke", squad.directive_id[:8]
                    )
                    self.revoke_directive(squad.directive_id, now)
                    continue
                cm.add_units(units)
                cm.execute(squad.target, squad.move_type)
            except Exception as exc:
                logger.debug("execute_tactics_step squad %s fail: %s", squad.directive_id[:8], exc)

    def _should_recon_retreat(self, squad_units: Any, target: Any) -> bool:
        """recon 撤退判定:敌方战场(target 18 grid 内)力量 × 1.2 >= squad 力量 → 撤。

        系数 1.2 比 probe 1.0 更宽松(recon 更小心:敌方稍弱也撤),跟用户定的语义
        "占便宜就占,占不到就撤"一致。target=None 或敌方没视野 → 不撤(继续走)。
        """
        if target is None:
            return False
        try:
            # 敌方在 target 周围 18 grid 内(同 sharpy DISTANCE_TO_INCLUDE)的单位
            enemy_local = self._bot.all_enemy_units.closer_than(18.0, target)
            # 排除农民(只看战斗单位)
            from sc2.ids.unit_typeid import UnitTypeId

            worker_types = {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
            enemy_local = enemy_local.filter(lambda u: u.type_id not in worker_types)
            if not enemy_local:
                return False  # 没敌方视野 → 不撤,继续走
            # 用 sharpy unit_values 算 power(跟 _should_retreat 一致)
            unit_values = self._bot.knowledge.unit_values
            squad_power = unit_values.calc_total_power(squad_units)
            enemy_power = unit_values.calc_total_power(enemy_local)
            # is_enough_for(other, our_percentage): self.power × our_percentage >= other.power
            # enemy_power × 1.2 >= squad_power 即"敌方占优" → 撤
            return enemy_power.is_enough_for(squad_power, 1.2)
        except Exception as exc:
            logger.debug("_should_recon_retreat fail: %s", exc)
            return False

    # ------------------------------------------------------------------
    # ParseContext 构造（从 facade.get_state + board 当前快照）
    # ------------------------------------------------------------------

    def build_parse_context(self, now: float) -> ParseContext:
        state = self.facade.get_state()
        active: dict[StageKind, str | None] = {}
        for stage in StageKind:
            slot = self.board.slots[stage]
            active[stage] = slot.strategy_id if slot is not None else None
        standing_orders = [
            f"{d.type.value}@{d.id[:6]}"
            for d in self.board.overlays
            if d.type == DirectiveType.UNIT_CLAIM
        ]
        return ParseContext(
            game_time=now,
            current_stage=self.board.current_stage,
            active_strategies=active,
            minerals=state.minerals,
            gas=state.gas,
            supply_used=state.supply_used,
            supply_cap=state.supply_cap,
            expansion_count=state.expansion_count,
            army_summary=dict(state.army_summary),
            enemy_summary=dict(state.enemy_summary),
            # 2026-05-28 用户:LLM 解析"补一个 BF"/"升级地面攻击"需要看
            # 当前建筑/升级状态推 delta(详见 rules.md)。
            buildings_summary=dict(state.buildings_summary),
            upgrades_done=sorted(state.upgrades),
            standing_orders=standing_orders,
            recent_commands=[c.text for c in self._recent_commands],
            recent_outcomes=[
                c.outcome_summary or _i18n_t("common.unparsed", self._lang)
                for c in self._recent_commands
            ],
            camera_point=getattr(self.facade, "get_camera_center", lambda: None)(),
        )

    # ------------------------------------------------------------------
    # 镜头坐标注入（Task C）
    # ------------------------------------------------------------------

    def _inject_camera_point(
        self,
        directives: list[Directive],
        camera_point: tuple[float, float] | None,
    ) -> None:
        """parse 后、submit 前：把 kind=CAMERA 的 TargetSpec 替换成镜头世界坐标。

        camera_point 为 None 时直接返回（没有镜头快照则不替换，防止崩溃）。
        覆盖：
          - UnitClaimPayload / MovePayload / ScoutPayload：
              task.primary_action.target 和 waypoints 里每个 TargetSpec
          - TacticalObjectivePayload：target_area == "camera" → 替换成 tuple
        只改 camera 标记，不动其他任何字段。
        """
        if camera_point is None:
            return
        from vibecraft.directives.models import (
            BuildAtPayload,
            ProductionOverridePayload,
            StealthMinePayload,
        )
        from vibecraft.directives.scope import TargetKind, TargetSpec

        _cam_str = f"({camera_point[0]}, {camera_point[1]})"

        def _patch_target(target: TargetSpec | None) -> None:
            # target 可为 None（如 verb=harass_workers 不指定矿区 → auto 轮换）。
            if target is None:
                return
            if target.kind == TargetKind.CAMERA and target.point is None:
                target.point = camera_point
            if target.waypoints:
                for wp in target.waypoints:
                    _patch_target(wp)

        def _patch_cond_area(cond: Any) -> None:
            # 2026-06-06 修复:done_when/activate_when 里 area=="camera" 也要注入坐标,
            # 否则 camera 类连续指令(在这里修X)的条件永远解析不出 → 链断 + 每帧刷警告。
            if cond is None:
                return
            try:
                if getattr(cond, "area", None) == "camera":
                    cond.area = _cam_str
            except Exception:
                pass
            for sub in getattr(cond, "conditions", None) or []:  # all_of/any_of 嵌套
                _patch_cond_area(sub)

        for d in directives:
            payload = d.payload
            if isinstance(
                payload, (UnitClaimPayload, MovePayload, ScoutPayload, RallyPointPayload)
            ):
                if isinstance(payload, UnitClaimPayload):
                    _patch_target(payload.task.primary_action.target)
                elif isinstance(payload, (MovePayload, ScoutPayload, RallyPointPayload)):
                    _patch_target(payload.target)
            elif isinstance(payload, TacticalObjectivePayload):
                if payload.target_area == "camera":
                    payload.target_area = camera_point
            elif isinstance(payload, ProductionOverridePayload):
                # 2026-06-07 "在这里刷N兵":warp_at=camera → 注入镜头坐标
                if payload.warp_at is not None:
                    _patch_target(payload.warp_at)
            elif isinstance(payload, BuildAtPayload):
                # 在这里(camera)修建筑:LLM 用 named_spot="camera",这里注入实际坐标点。
                if payload.named_spot == "camera" or (
                    payload.point is None and payload.named_spot is None
                ):
                    payload.point = camera_point
                    payload.named_spot = None
            elif isinstance(payload, StealthMinePayload):
                # 2026-06-10 WP7 偷矿:LLM 用 point=[0,0] 占位表示"用镜头坐标"。
                # Director 在此注入实际 camera_point（camera_point 已在函数入口确认非 None）。
                if payload.point == (0.0, 0.0):
                    payload.point = camera_point
            # 2026-07-08 用户补充1:"降落在这里/落这" → LLM 给 to_spot="camera",
            # 这里替换成真实镜头世界坐标 tuple(同 TacticalObjectivePayload.target_area
            # 的 camera 注入模式;_resolve_target_area 已原生支持 tuple)。
            elif isinstance(payload, StructureMovePayload) and payload.to_spot == "camera":
                payload.to_spot = camera_point
            # 条件里的 camera(done_when/activate_when,所有 payload 通用)
            _patch_cond_area(getattr(payload, "done_when", None))
            _patch_cond_area(getattr(payload, "activate_when", None))

    # ------------------------------------------------------------------
    # 镜头框选 selector 注入（F1，Task C 扩展）
    # ------------------------------------------------------------------

    def _inject_camera_selectors(
        self,
        directives: list[Directive],
        camera_point: tuple[float, float] | None,
    ) -> None:
        """parse 后、submit 前：把 selector.near_camera=True 的选择器一次性固化成具体 tags。

        near_camera=True 语义：采样**下达那刻**镜头视口矩形框(中心±12格宽 ±9格高)内、
        匹配条件的单位/建筑，写回 selector.tags，置 near_camera=False。
        此后该 selector 走普通 tags 分支，不再依赖镜头位置（持续/一次性指令统一）。

        camera_point=None（无镜头快照）→ tags=[] + near_camera=False + log warning。

        涉及的 payload 类型（所有带 Selector 字段的）：
          UnitClaimPayload.selector / MovePayload.selector / ScoutPayload.selector /
          UnitReleasePayload.selector / GroupAssignPayload.selector /
          ProductionOverridePayload.building_selector（building 选择器，走 unit_type 路径）。
        """
        import logging

        from vibecraft.directives.models import (
            GroupAssignPayload,
            MovePayload,
            ProductionOverridePayload,
            ScoutPayload,
            UnitClaimPayload,
            UnitReleasePayload,
        )

        _log = logging.getLogger(__name__)

        def _collect_selectors(payload: Any) -> list[Any]:
            """收集 payload 中所有 Selector 对象（含 None 过滤）。"""
            sels: list[Any] = []
            if isinstance(
                payload,
                (
                    UnitClaimPayload,
                    MovePayload,
                    UnitReleasePayload,
                    GroupAssignPayload,
                    ScoutPayload,
                ),
            ):
                if payload.selector is not None:
                    sels.append(payload.selector)
            elif (
                isinstance(payload, ProductionOverridePayload)
                and payload.building_selector is not None
            ):
                sels.append(payload.building_selector)
            return sels

        for d in directives:
            for sel in _collect_selectors(d.payload):
                if not getattr(sel, "near_camera", False):
                    continue

                if camera_point is None:
                    _log.warning(
                        "_inject_camera_selectors: near_camera=True 但 camera_point 为 None，"
                        "directive=%s selector 置空",
                        d.id,
                    )
                    sel.tags = []
                    sel.near_camera = False
                    continue

                # 按 unit_type 或 role 先获取候选 tags
                unit_type = getattr(sel, "unit_type", None)
                role = getattr(sel, "role", None)

                if unit_type is not None:
                    cand: list[int] = self.facade.resolve_selector(unit_type=unit_type) or []
                elif role == "ARMY":
                    cand = self.facade.all_own_unit_tags(include_workers=False)
                elif role in ("ANY", "IDLE", "LLM_CONTROLLED"):
                    cand = self.facade.all_own_unit_tags(include_workers=True)
                else:
                    # 守卫应已拒绝裸 near_camera；此处兜底
                    _log.warning(
                        "_inject_camera_selectors: near_camera=True 但无 unit_type/role，"
                        "directive=%s，跳过",
                        d.id,
                    )
                    sel.near_camera = False
                    continue

                # 盒过滤：(camera_center ± half_w=12, ± half_h=9)
                box = self.facade.filter_tags_in_box(
                    cand, camera_point[0], camera_point[1], 12.0, 9.0
                )

                # count 截断
                count = getattr(sel, "count", None)
                if count is not None and count > 0:
                    box = box[:count]

                # 一次固化：写回 tags，清 near_camera（之后该 selector 走普通 tags 分支）
                sel.tags = box
                sel.near_camera = False

    # ------------------------------------------------------------------
    # 内省（单测用）
    # ------------------------------------------------------------------

    @property
    def committed_count(self) -> int:
        return self._committed_count
