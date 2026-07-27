"""Room 状态机 + slot 仲裁单测（纯逻辑，无 IO）。

按 plan Task 3 的 11 条原始用例，加上评审修订补充：
- set_realtime 房主/非房主
- 3 真人 start 被拦（S1）
- in_game 态调 set_race 抛 RoomError
"""

import pytest

from vibecraft.server.room import Room, RoomError


def _room() -> Room:
    return Room(map_name="DaybreakLE", max_slots=4)


# ---- 原始 11 条用例 ----


def test_first_join_becomes_host_and_takes_slot0():
    r = _room()
    r.join("pid_a", "alice")
    assert r.host_player_id == "pid_a"
    assert r.slots[0].kind == "bot" and r.slots[0].player_id == "pid_a"


def test_join_assigns_next_open_slot():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    assert r.slots[1].player_id == "pid_b"


def test_set_race_and_team():
    r = _room()
    r.join("pid_a", "alice")
    r.set_race("pid_a", "Zerg")
    r.set_team("pid_a", 2)
    assert r.slots[0].race == "Zerg" and r.slots[0].team == 2


def test_host_adds_computer():
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Terran", difficulty="Hard")
    comp = [s for s in r.slots if s.kind == "computer"]
    assert len(comp) == 1 and comp[0].difficulty == "Hard"


def test_non_host_cannot_add_computer():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError):
        r.add_computer("pid_b", race="Terran", difficulty="Hard")


def test_start_requires_all_humans_ready_and_two_filled():
    r = _room()
    r.join("pid_a", "alice")
    with pytest.raises(RoomError):
        r.start("pid_a")  # 只有 1 个参与者，不能开
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    # 2026-06-12 房主免准备：alice 是房主，无需 set_ready 也能开局
    r.start("pid_a")
    assert r.state == "starting"


def test_leave_in_lobby_frees_slot_and_transfers_host():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.leave("pid_a")
    assert r.host_player_id == "pid_b"
    assert r.slots[0].kind == "open"


def test_rejoin_same_pid_is_idempotent():
    """同 pid 重连（手机刷新）不占第二个 slot。"""
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_a", "alice")
    assert sum(1 for s in r.slots if s.player_id == "pid_a") == 1


def test_state_transitions():
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryEasy")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    r.mark_in_game()
    assert r.state == "in_game"
    r.mark_ended()
    assert r.state == "lobby"  # 局终回 lobby，slot 保留、ready 清零
    assert r.slots[0].ready is False


def test_to_frame_shape():
    """room_state 下行帧的形状（PWA 依赖）。"""
    r = _room()
    r.join("pid_a", "alice")
    f = r.to_frame()
    assert f["type"] == "room_state"
    assert f["state"] == "lobby"
    assert f["host_player_id"] == "pid_a"
    assert f["slots"][0]["name"] == "alice"


def test_room_full_raises():
    """所有 slot 满后再加人抛 RoomError。"""
    r = Room(map_name="DaybreakLE", max_slots=2)
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError, match="满"):
        r.join("pid_c", "carol")


# ---- 评审修订补充用例 ----


def test_to_frame_contains_realtime():
    """to_frame() 必须带 realtime 字段（M4）。"""
    r = _room()
    r.join("pid_a", "alice")
    f = r.to_frame()
    assert "realtime" in f
    assert f["realtime"] is True  # 默认 True


def test_room_init_realtime_default_true():
    """Room() 默认 realtime=True（M4）。"""
    r = Room()
    assert r.realtime is True


def test_room_init_realtime_false():
    """Room(realtime=False) 能构造（M4）。"""
    r = Room(realtime=False)
    assert r.realtime is False
    f = r.to_frame()
    assert f["realtime"] is False


def test_set_realtime_host_in_lobby():
    """房主在 lobby 态可以切 realtime（M4）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.set_realtime("pid_a", False)
    assert r.realtime is False


def test_set_realtime_non_host_raises():
    """非房主调 set_realtime 抛 RoomError（M4）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError):
        r.set_realtime("pid_b", False)


def test_set_realtime_in_game_raises():
    """in_game 态调 set_realtime 抛 RoomError（M4，lobby only）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryEasy")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    r.mark_in_game()
    with pytest.raises(RoomError):
        r.set_realtime("pid_a", False)


def test_start_blocks_three_or_more_human_players():
    """3 个真人玩家 start 被拦（S1：实测过再放开）。"""
    r = Room(map_name="DaybreakLE", max_slots=4)
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.join("pid_c", "carol")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    r.set_ready("pid_c", True)
    with pytest.raises(RoomError, match="3\\+"):
        r.start("pid_a")


def test_set_race_in_game_raises():
    """in_game 态调 set_race 抛 RoomError（lobby only 校验）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryEasy")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    r.mark_in_game()
    with pytest.raises(RoomError):
        r.set_race("pid_a", "Zerg")


# ---- 引擎限制：多 agent 局仅纯 1v1（2026-06-12 spike 实测）----


def test_add_computer_rejected_when_two_humans():
    """已有 2 真人 → 加电脑被拦（SC2 多 agent 局仅 1v1）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError, match="1v1"):
        r.add_computer("pid_a", race="Terran", difficulty="Hard")


def test_start_rejected_when_two_humans_plus_computer():
    """先加电脑后进第二个真人 → start 被拦，提示移除电脑位。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Terran", difficulty="Hard")
    r.join("pid_b", "bob")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    with pytest.raises(RoomError, match="移除电脑"):
        r.start("pid_a")


def test_two_humans_pure_1v1_still_starts():
    """纯双真人 1v1 正常开局（引擎限制不误伤主路径）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    r.start("pid_a")
    assert r.state == "starting"


# ---- 2026-06-12 用户实测反馈:换位 + 指定位加电脑 ----


def test_take_slot_moves_player_keeps_state():
    """换到空位:种族/ready 随身走,原位清空,房主身份不变。"""
    r = _room()
    r.join("pid_a", "alice")
    r.set_race("pid_a", "Zerg")
    r.set_ready("pid_a", True)
    r.take_slot("pid_a", 2)
    assert r.slots[0].kind == "open"
    assert r.slots[2].player_id == "pid_a"
    assert r.slots[2].race == "Zerg" and r.slots[2].ready is True
    assert r.host_player_id == "pid_a"


def test_take_slot_rejects_occupied():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError, match="空位"):
        r.take_slot("pid_a", 1)  # bob 的位置


def test_take_slot_same_index_noop():
    r = _room()
    r.join("pid_a", "alice")
    r.take_slot("pid_a", 0)
    assert r.slots[0].player_id == "pid_a"


def test_add_computer_at_index():
    """指定空位加电脑(用户点某个空位)。"""
    r = _room()
    r.join("pid_a", "alice")
    s = r.add_computer("pid_a", race="Terran", difficulty="Hard", index=3)
    assert s.index == 3 and r.slots[3].kind == "computer"


def test_add_computer_at_occupied_index_rejected():
    r = _room()
    r.join("pid_a", "alice")
    with pytest.raises(RoomError, match="空位"):
        r.add_computer("pid_a", race="Terran", difficulty="Hard", index=0)


# ---- 2026-06-12 用户反馈 #3：房主免准备 ----


def test_host_not_ready_others_ready_start_succeeds():
    """房主未 set_ready，其他人已 ready → start 应成功（房主点开始=已就绪）。"""
    r = _room()
    r.join("host", "房主")
    r.join("guest", "客人")
    r.set_ready("guest", True)
    # host 没有 set_ready，但作为房主点开始 → 应成功
    r.start("host")
    assert r.state == "starting"


def test_host_not_ready_alone_with_computer_start_succeeds():
    """单人房主+电脑，房主未 ready → start 成功。"""
    r = _room()
    r.join("host", "房主")
    r.add_computer("host", race="Random", difficulty="VeryHard")
    # host 未 ready，仍能开局
    r.start("host")
    assert r.state == "starting"


def test_non_host_not_ready_still_blocked():
    """非房主未 ready → start 仍被拦（房主免准备只豁免房主自己）。"""
    r = _room()
    r.join("host", "房主")
    r.join("guest", "客人")
    r.set_ready("host", True)
    # guest 没 ready
    with pytest.raises(RoomError, match="未准备"):
        r.start("host")


# ---- #4 拒绝文案：游戏已开始第三人加入（2026-06-16）----


def test_join_in_game_new_player_rejected_with_correct_message():
    """对局进行中，新玩家加入 → 文案"无法加入"（区别于改设置用的"不能改房间设置"）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    r.start("pid_a")
    r.mark_in_game()
    with pytest.raises(RoomError, match="无法加入"):
        r.join("pid_new", "newcomer")


def test_join_in_game_reconnect_allowed():
    """对局进行中，已在房间的玩家重连（同 pid）→ 任何状态均可，不被拦。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    r.start("pid_a")
    r.mark_in_game()
    # 重连：同 pid → 更新显示名，不报错
    slot = r.join("pid_a", "alice_reconnected")
    assert slot.name == "alice_reconnected"


def test_set_race_in_game_message_unchanged():
    """in_game 态 set_race → 文案仍是"不能改房间设置"（_require_lobby 没被改动）。"""
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    r.start("pid_a")
    r.mark_in_game()
    with pytest.raises(RoomError, match="不能改房间设置"):
        r.set_race("pid_a", "Zerg")
