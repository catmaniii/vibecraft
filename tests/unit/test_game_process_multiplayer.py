"""GameConfig 多人字段 + 子进程多人分支的纯逻辑单测（Task 2 / 2026-06-12）。

不拉起 SC2——全部纯逻辑 + pickle 校验。
"""

import pickle

from vibecraft.server.game_process import GameConfig, _window_pid_allowed


def test_game_config_multiplayer_fields_default_solo() -> None:
    """默认 GameConfig 的多人字段应全为"单人"初始值（走原 run_multiple_games 路径）。"""
    cfg = GameConfig()
    assert cfg.mp_role == ""  # 空串 = 单人，不走多人分支
    assert cfg.mp_portconfig_json == ""
    assert cfg.mp_player_name == "VibeCraft"
    assert cfg.mp_guest_names == []
    assert cfg.mp_computers == []
    assert cfg.mp_game_time_limit == 7200


def test_game_config_multiplayer_picklable() -> None:
    """GameConfig 多人字段跨 spawn 边界必须可 pickle（multiprocessing.spawn 要求）。"""
    cfg = GameConfig(
        mp_role="host",
        mp_portconfig_json='{"server": [1, 2], "players": [[3, 4]]}',
        mp_guest_names=["bob"],
        mp_computers=[{"race": "Terran", "difficulty": "Hard"}],
        mp_player_name="alice",
        mp_game_time_limit=3600,
    )
    restored = pickle.loads(pickle.dumps(cfg))
    assert restored.mp_role == "host"
    assert restored.mp_portconfig_json == '{"server": [1, 2], "players": [[3, 4]]}'
    assert restored.mp_guest_names == ["bob"]
    assert restored.mp_computers == [{"race": "Terran", "difficulty": "Hard"}]
    assert restored.mp_player_name == "alice"
    assert restored.mp_game_time_limit == 3600
    # join 方也可 pickle
    cfg_join = GameConfig(mp_role="join", mp_portconfig_json='{"server":[5,6],"players":[[7,8]]}')
    assert pickle.loads(pickle.dumps(cfg_join)).mp_role == "join"


def test_window_pid_allowed_pure_logic() -> None:
    """_window_pid_allowed 纯函数——白名单过滤逻辑（不依赖 Windows API）。

    抽出来单独测，避免对整个 _focus_sc2_window 做重 mock。
    """
    # whitelist=None → 允许所有（老行为兼容）
    assert _window_pid_allowed(1234, None) is True
    assert _window_pid_allowed(0, None) is True

    # 在白名单内 → True
    assert _window_pid_allowed(1234, {1234, 5678}) is True
    assert _window_pid_allowed(5678, {1234, 5678}) is True

    # 不在白名单 → False（跳过该窗口）
    assert _window_pid_allowed(9999, {1234, 5678}) is False
    assert _window_pid_allowed(0, {1234}) is False

    # 空白名单 → 全不允许
    assert _window_pid_allowed(1234, set()) is False
