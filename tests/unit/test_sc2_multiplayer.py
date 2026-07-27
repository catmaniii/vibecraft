"""sc2_multiplayer runner 的纯逻辑单测（不拉起 SC2）。

测两件事：
1. build_host_players 的排列顺序（本方 bot 在前、guest 占位居中、Computer 殿后）
2. new_portconfig_json：散点端口 + round-trip 后端口不变（跨进程传递的根基）

两者均不拉起 SC2——portpicker 是 sc2 的依赖，单测环境可用；
host_game / join_game 需要 SC2 进程，不在此处测。
"""

import json

from vibecraft.server.sc2_multiplayer import build_host_players, new_portconfig_json


def test_build_host_players_orders_bots_before_computers() -> None:
    """host create_game 的 players：本方 bot 在前，guest 占位 bot 居中，Computer 殿后。"""
    players = build_host_players(
        my_race="Protoss",
        my_name="alice",
        guest_names=["bob"],
        computers=[{"race": "Terran", "difficulty": "Hard"}],
    )
    # 3 个玩家：alice(本方), bob(guest 占位), 1 个 Computer
    assert len(players) == 3
    assert players[0].name == "alice"
    assert players[1].name == "bob"
    # 按类名断言而非 isinstance：全量 suite 里有测试会 stub/重载 sc2 模块，
    # isinstance 的类身份在 cross-test 下不稳定（2026-06-12 踩坑）
    assert players[2].__class__.__name__ == "Computer"


def test_new_portconfig_json_shape() -> None:
    """new_portconfig_json：round-trip 后端口结构不变（跨进程传递的根基）。"""
    pc_json = new_portconfig_json(guests=1)
    data = json.loads(pc_json)
    # server 端口对（2 个）
    assert len(data["server"]) == 2
    # players 端口对列表：guests=1 → 1 对各含 2 个端口
    assert len(data["players"]) == 1 and len(data["players"][0]) == 2


def test_new_portconfig_json_ports_unique() -> None:
    """4 个端口互不重复。

    注：**不能**用"非连号"做断言——Windows 顺序分配临时端口，散点 Portconfig()
    四连 pick 也常拿到连号；它安全的原因是端口都真实 pick 过（游标推过去了），
    而 contiguous_ports 只检查空闲不推游标 → 被子进程 SC2 的 ws 端口撞上
    （2026-06-12 spike 实锤）。该坑无法用纯单测防回归，靠 new_portconfig_json
    docstring + multiplayer_smoke.py --contiguous-ports 复现路径兜底。
    """
    data = json.loads(new_portconfig_json(guests=1))
    ports = [*data["server"], *data["players"][0]]
    assert len(set(ports)) == 4
