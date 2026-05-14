"""build_translator 单元测试。

覆盖 M1.5 需求：
- 每种 BuildStep verb（build / train / research / send_probe）翻译正确
- @chrono modifier 插入额外 CHRONO 步骤
- @chrono 附带升级时指向正确建筑
- scout_at 翻译为 WORKER_SCOUT
- 完整 opening（1g_robo_immortal）端到端翻译
- openings_to_ares_config_builds 生成正确的 config["Builds"] 结构
"""

from __future__ import annotations

import pytest

from voicecraft.bot.build_translator import (
    opening_to_ares_builds_entry,
    openings_to_ares_config_builds,
    translate_opening_to_ares_steps,
)
from voicecraft.strategy.models import OpeningBuild

# ---------------------------------------------------------------------------
# 工具函数：从字符串快速构造单步 OpeningBuild
# ---------------------------------------------------------------------------


def _make_opening(
    steps: list[str],
    scout_at: str | None = None,
    opening_id: str = "test_opening",
) -> OpeningBuild:
    return OpeningBuild.model_validate(
        {
            "kind": "opening_build",
            "id": opening_id,
            "display_name_zh": "测试",
            "phases": [{"id": "p1", "display": "P1"}],
            "steps": steps,
            "scout_at": scout_at,
        }
    )


# ---------------------------------------------------------------------------
# 单步翻译
# ---------------------------------------------------------------------------


class TestTranslateBuildVerb:
    """build 动词：结构名直接大写。"""

    def test_pylon(self) -> None:
        opening = _make_opening(["13 build Pylon"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["13 PYLON"]

    def test_gateway(self) -> None:
        opening = _make_opening(["14 build Gateway"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["14 GATEWAY"]

    def test_assimilator(self) -> None:
        opening = _make_opening(["14 build Assimilator"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["14 ASSIMILATOR"]

    def test_nexus(self) -> None:
        opening = _make_opening(["20 build Nexus"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["20 NEXUS"]

    def test_cyberneticscore(self) -> None:
        opening = _make_opening(["17 build CyberneticsCore"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["17 CYBERNETICSCORE"]

    def test_roboticsfacility(self) -> None:
        opening = _make_opening(["24 build RoboticsFacility"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["24 ROBOTICSFACILITY"]


class TestTranslateTrainVerb:
    """train 动词：单位名直接大写。"""

    def test_immortal(self) -> None:
        opening = _make_opening(["34 train Immortal"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["34 IMMORTAL"]

    def test_observer(self) -> None:
        opening = _make_opening(["32 train Observer"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["32 OBSERVER"]


class TestTranslateResearchVerb:
    """research 动词：升级 ID 大写，@chrono 插额外步骤。"""

    def test_research_without_chrono(self) -> None:
        opening = _make_opening(["22 research WarpGateResearch"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["22 WARPGATERESEARCH"]

    def test_research_with_chrono_inserts_chrono_step(self) -> None:
        opening = _make_opening(["22 research WarpGateResearch @chrono"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["22 WARPGATERESEARCH", "22 CHRONO @ CYBERNETICSCORE"]

    def test_research_chrono_blink_targets_twilight(self) -> None:
        """Blink 研究自 TwilightCouncil。"""
        opening = _make_opening(["60 research BlinkTech @chrono"])
        steps = translate_opening_to_ares_steps(opening)
        assert "60 CHRONO @ TWILIGHTCOUNCIL" in steps

    def test_research_unknown_upgrade_falls_back_to_nexus(self) -> None:
        """未在映射表里的 upgrade chrono fallback 到 NEXUS。"""
        opening = _make_opening(["40 research SomeNewUpgrade @chrono"])
        steps = translate_opening_to_ares_steps(opening)
        # 有 CHRONO 步骤，且 target 是 NEXUS
        chrono_steps = [s for s in steps if "CHRONO" in s]
        assert len(chrono_steps) == 1
        assert "NEXUS" in chrono_steps[0]


class TestTranslateSendProbeVerb:
    """send_probe → WORKER_SCOUT（忽略 target）。"""

    def test_send_probe_to_enemy_natural(self) -> None:
        opening = _make_opening(
            steps=["13 build Pylon"],
            scout_at="17 send_probe enemy_natural",
        )
        steps = translate_opening_to_ares_steps(opening)
        assert "17 WORKER_SCOUT" in steps

    def test_send_probe_in_steps_list(self) -> None:
        """send_probe 作为 steps 列表里的一项（理论上少见，但要兼容）。"""
        opening = _make_opening(["17 send_probe enemy_natural"])
        steps = translate_opening_to_ares_steps(opening)
        assert steps == ["17 WORKER_SCOUT"]


# ---------------------------------------------------------------------------
# scout_at 处理
# ---------------------------------------------------------------------------


class TestScoutAt:
    def test_scout_at_appended_at_end(self) -> None:
        opening = _make_opening(
            steps=["13 build Pylon", "14 build Gateway"],
            scout_at="17 send_probe enemy_natural",
        )
        steps = translate_opening_to_ares_steps(opening)
        # WORKER_SCOUT 应出现在所有结构步骤之后
        assert steps[-1] == "17 WORKER_SCOUT"
        assert steps[0] == "13 PYLON"

    def test_no_scout_at_excluded(self) -> None:
        opening = _make_opening(["13 build Pylon"])
        steps = translate_opening_to_ares_steps(opening)
        assert "WORKER_SCOUT" not in " ".join(steps)


# ---------------------------------------------------------------------------
# 多步骤顺序
# ---------------------------------------------------------------------------


class TestStepOrder:
    def test_chrono_step_inserted_after_research_step(self) -> None:
        opening = _make_opening(
            ["22 research WarpGateResearch @chrono", "24 build RoboticsFacility"]
        )
        steps = translate_opening_to_ares_steps(opening)
        idx_research = steps.index("22 WARPGATERESEARCH")
        idx_chrono = steps.index("22 CHRONO @ CYBERNETICSCORE")
        idx_robo = steps.index("24 ROBOTICSFACILITY")
        assert idx_research < idx_chrono < idx_robo


# ---------------------------------------------------------------------------
# 完整 opening 端到端
# ---------------------------------------------------------------------------


class TestFullOpening1gRoboImmortal:
    """用真实 1g_robo_immortal steps 做端到端翻译校验。"""

    @pytest.fixture
    def opening(self) -> OpeningBuild:
        return OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "1g_robo_immortal",
                "display_name_zh": "1门Robo 不朽开",
                "phases": [{"id": "p1", "display": "P1"}],
                "steps": [
                    "13 build Pylon",
                    "14 build Gateway",
                    "14 build Assimilator",
                    "16 build Pylon",
                    "17 build CyberneticsCore",
                    "20 build Nexus",
                    "21 build Assimilator",
                    "22 research WarpGateResearch @chrono",
                    "24 build RoboticsFacility",
                    "32 train Observer",
                    "34 train Immortal",
                ],
                "scout_at": "17 send_probe enemy_natural",
            }
        )

    def test_step_count(self, opening: OpeningBuild) -> None:
        steps = translate_opening_to_ares_steps(opening)
        # 11 steps + 1 chrono + 1 scout = 13
        assert len(steps) == 13

    def test_first_step(self, opening: OpeningBuild) -> None:
        steps = translate_opening_to_ares_steps(opening)
        assert steps[0] == "13 PYLON"

    def test_all_expected_steps_present(self, opening: OpeningBuild) -> None:
        steps = translate_opening_to_ares_steps(opening)
        expected_subset = [
            "14 GATEWAY",
            "14 ASSIMILATOR",
            "17 CYBERNETICSCORE",
            "20 NEXUS",
            "22 WARPGATERESEARCH",
            "22 CHRONO @ CYBERNETICSCORE",
            "24 ROBOTICSFACILITY",
            "32 OBSERVER",
            "34 IMMORTAL",
            "17 WORKER_SCOUT",
        ]
        for expected in expected_subset:
            assert expected in steps, f"步骤 {expected!r} 不在翻译结果中"


# ---------------------------------------------------------------------------
# opening_to_ares_builds_entry + openings_to_ares_config_builds
# ---------------------------------------------------------------------------


class TestAresConfigShape:
    def test_entry_has_opening_build_order_key(self) -> None:
        opening = _make_opening(["13 build Pylon"])
        entry = opening_to_ares_builds_entry(opening)
        assert "OpeningBuildOrder" in entry
        assert isinstance(entry["OpeningBuildOrder"], list)

    def test_multiple_openings_to_builds_dict(self) -> None:
        op1 = _make_opening(["13 build Pylon"], opening_id="build_a")
        op2 = _make_opening(["14 build Gateway"], opening_id="build_b")
        builds = openings_to_ares_config_builds([op1, op2])
        assert "build_a" in builds
        assert "build_b" in builds
        # 每个 entry 都有 OpeningBuildOrder
        assert "OpeningBuildOrder" in builds["build_a"]
        assert "OpeningBuildOrder" in builds["build_b"]

    def test_builds_dict_step_values(self) -> None:
        op = _make_opening(["13 build Pylon", "14 build Gateway"])
        builds = openings_to_ares_config_builds([op])
        order: list[str] = builds["test_opening"]["OpeningBuildOrder"]  # type: ignore[index]
        assert order == ["13 PYLON", "14 GATEWAY"]
