"""_SharpyFacadeBase.set_engagement_stance 行为回归（2026-06-13 真机大坑）。

事故：Director revoke_tactical 用 set_engagement_stance(None) 清姿态，真机实现
None 落到 else no-op → 玩家 × 防守/撤退后 stance_override 永远卡住 → sharpy
_should_attack 恒 False → bot 余生不再自主进攻（实测多人局日志：538s intent
清了、stance 钉死 "defend" 到终局）。FakeFacade 只记录不判断 → 单测全绿测不出。
本文件直接打真机 _SharpyFacadeBase，把这个不对称挡在单测里。
"""

from __future__ import annotations

from types import SimpleNamespace


def _make_facade():
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()
    bot = SimpleNamespace(
        knowledge=SimpleNamespace(vibecraft=SimpleNamespace(stance_override=None))
    )
    f = cls.__new__(cls)
    f.bot = bot
    return f, bot


def test_set_stance_defend_then_none_clears() -> None:
    f, bot = _make_facade()
    f.set_engagement_stance("defend")
    assert bot.knowledge.vibecraft.stance_override == "defend"
    f.set_engagement_stance(None)  # Director revoke_tactical 的真实调用方式
    assert bot.knowledge.vibecraft.stance_override is None


def test_set_stance_free_clears() -> None:
    f, bot = _make_facade()
    f.set_engagement_stance("retreat")
    assert bot.knowledge.vibecraft.stance_override == "retreat"
    f.set_engagement_stance("free")
    assert bot.knowledge.vibecraft.stance_override is None


def test_set_stance_hold_and_retreat() -> None:
    f, bot = _make_facade()
    for s in ("defend", "hold", "retreat"):
        f.set_engagement_stance(s)
        assert bot.knowledge.vibecraft.stance_override == s


def test_unknown_stance_is_noop() -> None:
    f, bot = _make_facade()
    f.set_engagement_stance("defend")
    f.set_engagement_stance("banana")  # 未知值不改状态(只 warning)
    assert bot.knowledge.vibecraft.stance_override == "defend"
