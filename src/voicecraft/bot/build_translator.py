"""voicecraft OpeningBuild → ares config["Builds"] 翻译层。

设计：纯函数，不 import ares/sc2，完全可单测。

ares config["Builds"] 的预期格式（来自 build_order_runner.py spike）：
    {
        "Builds": {
            "<build_id>": {
                "OpeningBuildOrder": ["<supply> <CMD>", ...]
            }
        }
    }

步骤翻译规则（spike A 结论）：
- ares parser 先尝试 UnitID[cmd.upper()]，再试 UpgradeId[cmd.upper()]，
  最后才走 BuildOrderOptions[cmd.upper()]。
- 所以直接传大写结构/单位名即可：PYLON / GATEWAY / ASSIMILATOR / NEXUS /
  CYBERNETICSCORE / ROBOTICSFACILITY / IMMORTAL / OBSERVER ...
- `build`  → 结构名直接大写（PYLON / GATEWAY 等）
- `train`  → 单位名直接大写（IMMORTAL / OBSERVER 等）
- `research` → 升级 ID 大写（WARPGATERESEARCH 等）
  - `@chrono` modifier → 额外插入 "<supply> CHRONO @ <from_structure>" 步骤
- `send_probe` → WORKER_SCOUT（忽略 target，ares 自动寻路到敌基地）

chrono target 映射（upgrade → 研究建筑大写名，仅列已用到的）：
    WARPGATERESEARCH → CYBERNETICSCORE
    CHARGE / BLINK / SHADOWSTRIKE → TWILIGHTCOUNCIL
    PHOENIXRANGEUPGRADE → STARGATE
    GRAVITICDRIVE → ROBOTICSBAY
    EXTENDEDTHERMALLANCE → ROBOTICSBAY

未在 M1.5 范围内的 step kind：
- `midgame_stance` / `lategame_doctrine` 不经过 ares build runner，不翻译。
- `abort_signals` / `default_transitions` / `phases` 归 voicecraft Board/DSL（M2+）。
"""

from __future__ import annotations

from voicecraft.strategy.models import BuildStep, OpeningBuild

# ares 常量（字符串，不 import ares）
_BUILDS_KEY = "Builds"
_OPENING_BUILD_ORDER_KEY = "OpeningBuildOrder"
_CONSTANT_WORKER_PRODUCTION_TILL_KEY = "ConstantWorkerProductionTill"
_AUTO_SUPPLY_AT_SUPPLY_KEY = "AutoSupplyAtSupply"

# ares build runner 的 opening 期背景行为参数。voicecraft 剧本 steps 只列关键
# 建筑/单位时序，"持续造农民" + "supply 不堵" 是 ares 的背景行为，必须在 config
# 里显式开启 —— ares 默认 ConstantWorkerProductionTill=0 即完全不造农民。
# M2+ 可做成 OpeningBuild 的可配字段。
_CONSTANT_WORKER_PRODUCTION_TILL = 44  # 持续造农民到 44 工人（神族双矿满采）
_AUTO_SUPPLY_AT_SUPPLY = 14  # supply 到 14 起 ares 自动补 Pylon，防 supply 堵

# upgrade → 研究建筑名（ares 步骤里 CHRONO 的 target）
# 用大写 UnitID 名，与 ares parser 期望一致。
_UPGRADE_TO_BUILDING: dict[str, str] = {
    "WARPGATERESEARCH": "CYBERNETICSCORE",
    "BLINKTECH": "TWILIGHTCOUNCIL",
    "CHARGE": "TWILIGHTCOUNCIL",
    "SHADOWSTRIKE": "TWILIGHTCOUNCIL",
    "PHOENIXRANGEUPGRADE": "STARGATE",
    "GRAVITICDRIVE": "ROBOTICSBAY",
    "EXTENDEDTHERMALLANCE": "ROBOTICSBAY",
    "PSISTORMTECH": "TEMPLARARCHIVE",
    "VOIDRAYSPEEDUPGRADE": "FLEETBEACON",
    "CARRIERLAUNCHSPEEDUPGRADE": "FLEETBEACON",
}


def translate_opening_to_ares_steps(opening: OpeningBuild) -> list[str]:
    """voicecraft OpeningBuild 的 steps + scout_at → ares build order step 字符串列表。

    返回值可直接作为
    ``config["Builds"][opening.id]["OpeningBuildOrder"]``。
    """
    result: list[str] = []

    for raw in opening.steps:
        step = BuildStep.parse(raw)
        translated = _translate_step(step)
        result.extend(translated)

    if opening.scout_at is not None:
        scout_step = BuildStep.parse(opening.scout_at)
        result.extend(_translate_step(scout_step))

    return result


def opening_to_ares_builds_entry(opening: OpeningBuild) -> dict[str, object]:
    """单个 opening 翻译为 ares config["Builds"]["<id>"] 的 dict。"""
    return {
        _OPENING_BUILD_ORDER_KEY: translate_opening_to_ares_steps(opening),
        _CONSTANT_WORKER_PRODUCTION_TILL_KEY: _CONSTANT_WORKER_PRODUCTION_TILL,
        _AUTO_SUPPLY_AT_SUPPLY_KEY: _AUTO_SUPPLY_AT_SUPPLY,
    }


def openings_to_ares_config_builds(openings: list[OpeningBuild]) -> dict[str, object]:
    """多个 opening → config["Builds"] dict（直接合并到 bot.config）。

    用法：
        bot.config.setdefault("Builds", {}).update(
            openings_to_ares_config_builds([...])
        )
    """
    return {op.id: opening_to_ares_builds_entry(op) for op in openings}


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _translate_step(step: BuildStep) -> list[str]:
    """单个 BuildStep → 0-N 条 ares 步骤字符串。

    大部分步骤翻译为 1 条；`@chrono` 会额外追加 1 条 CHRONO 步骤。
    ``send_probe`` 翻译为 WORKER_SCOUT（忽略 obj target；ares 自动寻路）。
    """
    supply = step.supply
    obj_upper = step.obj.upper()

    if step.verb == "send_probe":
        # send_probe <target> → WORKER_SCOUT（ares 自动选目标）
        return [f"{supply} WORKER_SCOUT"]

    if step.verb == "research":
        # research WarpGateResearch [@chrono] → WARPGATERESEARCH [+ CHRONO @ BC]
        steps = [f"{supply} {obj_upper}"]
        if step.modifier == "chrono":
            building = _chrono_building_for_upgrade(obj_upper)
            steps.append(f"{supply} CHRONO @ {building}")
        return steps

    # build / train → 直接大写名
    # ares parser: UnitID[cmd.upper()] 能匹配 PYLON / GATEWAY / IMMORTAL 等
    steps = [f"{supply} {obj_upper}"]
    if step.modifier == "chrono":
        # build 步骤上的 @chrono：对结构本身做 chrono
        steps.append(f"{supply} CHRONO @ {obj_upper}")
    return steps


def _chrono_building_for_upgrade(upgrade_upper: str) -> str:
    """返回研究某 upgrade 所用的建筑大写名。

    优先查内置映射表；未找到时回退到 NEXUS（保底，让 ares chrono 不崩）。
    """
    return _UPGRADE_TO_BUILDING.get(upgrade_upper, "NEXUS")
