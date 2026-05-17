"""14 个 ExpectedSpec —— mirror scripts/e2e_4_directive_types.py 的 CASES。

每个 spec 定义:
- inject:玩家原话
- expect_type:期望的 DirectiveType
- must_have_paths:必须满足的字段(JSON path → value/list/predicate)
- forbidden_paths:不允许的字段值
- allow_extra_directives:是否容忍 LLM 多生成

跟 e2e 的差异:e2e 看 sharpy/director/board 集成结果(snapshot 字段 + events
流);这里只看 LLM 解析的 directive 列表对错。
"""

from __future__ import annotations

from vibecraft.directives.types import DirectiveType

from tests.llm_eval.score import ExpectedSpec

LLM_EVAL_CASES: list[ExpectedSpec] = [
    # ---- L1 宏观策略 ----
    ExpectedSpec(
        name="L1a_strategy_set",
        inject="切叉球一波",
        expect_type=DirectiveType.STRATEGY_SET,
        must_have_paths={
            "payload.stage": "midgame",
            "payload.strategy_id": "iac_2base",
        },
    ),
    ExpectedSpec(
        name="L1b_strategy_cancel",
        inject="取消所有剧本",
        expect_type=DirectiveType.STRATEGY_CANCEL,
        must_have_paths={"payload.stage": "all"},
    ),
    # ---- L2 战术目标 (tactical_objective + engagement_constraint) ----
    ExpectedSpec(
        name="L2a_tactical_attack",
        inject="进攻对方自然",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": "attack",
            "payload.target_area": "enemy_natural",
        },
    ),
    ExpectedSpec(
        name="L2b_tactical_scout_vision",
        inject="看一眼对方主基地",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": ["scout", "vision"],  # 两者都接受
            "payload.target_area": "enemy_main",
        },
    ),
    ExpectedSpec(
        name="L2c_tactical_harass_killed",
        inject="凤凰打死对方 5 个农民就回",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": "harass",
        },
    ),
    ExpectedSpec(
        name="L2d_engagement_defend",
        inject="守家别出门",
        expect_type=DirectiveType.ENGAGEMENT_CONSTRAINT,
        must_have_paths={"payload.stance": "defend"},
        forbidden_paths={"payload.stance": ["hold_position", "守家", "guard"]},
    ),
    ExpectedSpec(
        name="L2e_engagement_retreat_timer",
        inject="30 秒后撤",
        expect_type=DirectiveType.ENGAGEMENT_CONSTRAINT,
        must_have_paths={"payload.stance": "retreat"},
    ),
    # ---- L3 单位 / 常驻 / 建造 ----
    ExpectedSpec(
        name="L3a_unit_claim_persistent",
        inject="探机巡逻自然别动",
        expect_type=DirectiveType.UNIT_CLAIM,
        must_have_paths={
            "payload.selector.unit_type": "Probe",
            "payload.persistent": True,
            "payload.task.primary_action.verb": ["patrol", "hold_position", "guard_position"],
        },
    ),
    ExpectedSpec(
        name="L3b_unit_claim_ephemeral",
        inject="让那个探机移动到气矿",
        expect_type=DirectiveType.UNIT_CLAIM,
        must_have_paths={
            "payload.selector.unit_type": "Probe",
            "payload.task.primary_action.verb": "move_to",
            "payload.persistent": False,
        },
        forbidden_paths={
            "payload.task.primary_action.verb": ["scout", "move", "gather", "guard"],
        },
    ),
    ExpectedSpec(
        name="L3c_scout",
        inject="侦察一下对方主基地",
        expect_type=DirectiveType.SCOUT,
        must_have_paths={
            # SCOUT directive 的 target 字段(不进 unit_claim.task.verb=scout)
            "payload.target.named_spot": "enemy_main",
        },
    ),
    ExpectedSpec(
        name="L3d_engagement_hold",
        inject="所有人原地待命别动",
        expect_type=DirectiveType.ENGAGEMENT_CONSTRAINT,
        must_have_paths={"payload.stance": "hold"},
        forbidden_paths={"payload.stance": ["hold_position", "guard", "defend"]},
    ),
    # ---- L4 产能调整 ----
    ExpectedSpec(
        name="L4a_production_override_count",
        inject="下个 BG 出俩哨兵",
        expect_type=DirectiveType.PRODUCTION_OVERRIDE,
        must_have_paths={
            "payload.unit_type": "Sentry",
            "payload.count": 2,
        },
    ),
    ExpectedSpec(
        name="L4b_tech_override",
        inject="先研闪烁",
        expect_type=DirectiveType.TECH_OVERRIDE,
        must_have_paths={
            "payload.upgrade_id": ["BlinkTech", "Blink", "BLINKTECH"],
        },
    ),
    ExpectedSpec(
        name="L4c_expansion_override",
        inject="马上去开三矿",
        expect_type=DirectiveType.EXPANSION_OVERRIDE,
        must_have_paths={"payload.target_count": 3},
    ),
]
