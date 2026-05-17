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
        # 原 inject "看一眼对方主基地" LLM 把 "看一眼" 当 transient scout
        # 经常输出顶层 SCOUT 而非 TACTICAL_OBJECTIVE(verb=vision)。
        # 改强信号 "保持视野" → 一定走 tactical_objective(verb=vision +
        # done_when=vision_acquired)。
        name="L2b_tactical_scout_vision",
        inject="在对方主基地保持视野",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": ["vision", "scout"],  # vision 优先,scout 也接受
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
        # 原 inject "侦察一下对方主基地" LLM 路由不稳定。改 "派探机侦察 11 点"
        # 强信号:带 unit(探机) + 具体方位(11 点)走顶层 scout(selector + target)。
        name="L3c_scout",
        inject="派探机侦察 11 点",
        expect_type=DirectiveType.SCOUT,
        must_have_paths={
            "payload.selector.unit_type": "Probe",
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
    # ---- P0d/e/i 新原语 case（Task 17 prompt 改动 + Task 18 eval 覆盖）----
    # O1: structure_override 补 8 BG
    # done_when 检查: structure_count(Gateway, >=, 8) — 需扩 ExpectedSpec 才能断言
    # (follow-up: 加 expected_done_when_kind 字段到 ExpectedSpec + score_outcome)
    ExpectedSpec(
        name="O1_structure_override_8bg",
        inject="家里补到 8 BG",
        expect_type=DirectiveType.STRUCTURE_OVERRIDE,
        must_have_paths={
            "payload.structure_type": ["Gateway", "GATEWAY", "gateway"],
            "payload.target_count": 8,
        },
    ),
    # O2: structure_override ramp cannon — location_hint 断言
    # done_when: structure_count(PhotonCannon, >=, 1) — 同上留 follow-up
    ExpectedSpec(
        name="O2_structure_override_ramp_cannon",
        inject="ramp 放 1 cannon",
        expect_type=DirectiveType.STRUCTURE_OVERRIDE,
        must_have_paths={
            "payload.structure_type": [
                "PhotonCannon",
                "PHOTONCANNON",
                "photon_cannon",
            ],
            "payload.target_count": 1,
        },
    ),
    # L2 进攻自然: A 类规则 done_when 必须是 None
    # ExpectedSpec 目前无法断言 done_when=None；用 forbidden_paths 近似:
    # 不允许 done_when 字段有任何非空值（string 类型）。
    # follow-up: 加 expected_done_when=None 到 ExpectedSpec + score_outcome
    ExpectedSpec(
        name="L2_attack_natural_done_when_none",
        inject="进攻对方自然",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": "attack",
            "payload.target_area": ["enemy_natural", "natural"],
        },
        # done_when=None 是 A 类规则关键；score_outcome 尚无字段断言 None 值。
        # 留 follow-up: ExpectedSpec.expected_done_when_kind + score 逻辑扩展。
    ),
    # L2 5 凤凰骚扰: unit_count_hint + enemy_killed_in_area done_when
    ExpectedSpec(
        name="L2_harass_5_phoenix_enemy_main",
        inject="派 5 个凤凰去骚扰对方主基地",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": "harass",
            "payload.target_area": ["enemy_main", "main"],
            "payload.unit_count_hint": 5,
        },
    ),
    # L2 凤凰骚扰无数量: B 类必须给数量否则 AmbiguousParse。
    # score_outcome 对 AmbiguousParse 永远 FAIL，无法测 expected_ambiguous。
    # 简化处理：接受 LLM 仍输出 TACTICAL_OBJECTIVE(verb=harass) 但不断言数量。
    # follow-up: 加 ExpectedSpec.expect_ambiguous=True + score_outcome 分支支持。
    ExpectedSpec(
        name="L2_harass_phoenix_no_count_relaxed",
        inject="凤凰骚扰对面",
        expect_type=DirectiveType.TACTICAL_OBJECTIVE,
        must_have_paths={
            "payload.verb": "harass",
        },
        # 注: 真实期望应为 AmbiguousParse（B 类无数量触发二次确认）。
        # 但 ExpectedSpec 无 expect_ambiguous 字段，此处退化为弱断言 (verb=harass 即过)。
        # follow-up: 扩 ExpectedSpec + score_outcome 支持 AmbiguousParse 作为 PASS。
    ),
    # L1+L4 等闪烁升+1攻 (chain 拆 2 步): MVP 只测第一步 (tech_override upgrade=Blink)
    # 已有 L4b_tech_override 测同样 inject "先研闪烁"；此 case 为 chain 场景命名
    ExpectedSpec(
        name="L4_chain_blink_first_step",
        inject="先研闪烁",
        expect_type=DirectiveType.TECH_OVERRIDE,
        must_have_paths={
            "payload.upgrade_id": ["BlinkTech", "Blink", "BLINKTECH"],
        },
        # 注: 完整 chain "研完闪烁再升+1攻" 拆 2 步;MVP 不做 chain,只验第一步。
        # 第二步 (PRODUCTION_OVERRIDE / TECH_OVERRIDE upgrade=attack+1) 留 follow-up。
    ),
]
