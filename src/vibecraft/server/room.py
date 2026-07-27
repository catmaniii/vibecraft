"""房间状态机 + slot 模型（阶段 0 多人联网，纯逻辑无 IO）。

设计：docs/plans/2026-06-12-multiplayer-design.md §3.2。
状态机：lobby → starting → in_game → (ended 瞬态) → lobby。

v0 一个 server 一个房间；team 字段进模型/UI，引擎层同盟以 Task 1 spike 结论为准。

评审修订（2026-06-12 Opus）已叠加：
- M4：realtime 字段 + set_realtime 方法（房主 + lobby only）
- S1：start() 拦截 3+ 真人（实测后放开）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

# slot 类型：open=空位 / bot=真人玩家 / computer=内置 AI / closed=已关闭
SlotKind = Literal["open", "bot", "computer", "closed"]

# 房间状态：lobby=大厅 / starting=启动中 / in_game=对局中
RoomState = Literal["lobby", "starting", "in_game"]


class RoomError(Exception):
    """房间操作被拒（带玩家可读原因，WS 层转 toast）。

    带 i18n ``key`` 时 WS 层按玩家 locale 本地化（``localized()``）；``str(self)`` 始终返回
    zh 文本（日志 / 无 key 回退 / 既有测试的 ``match=`` 子串断言）。
    """

    def __init__(self, zh: str, *, key: str | None = None, **params: object) -> None:
        super().__init__(zh)
        self.zh = zh
        self.key = key
        self.params = params

    def localized(self, lang: str) -> str:
        """按玩家语言渲染；无 key 回退 zh 原文。"""
        if self.key is None:
            return self.zh
        from vibecraft.i18n import t

        return t(self.key, lang, **self.params)


@dataclass
class Slot:
    """单个玩家/AI 位置。"""

    index: int
    kind: SlotKind = "open"
    team: int = 1
    race: str = "Protoss"
    difficulty: str = "VeryHard"  # kind=computer 时有效
    player_id: str = ""  # kind=bot 时绑定的玩家
    name: str = ""  # 显示名（玩家用户名 / "电脑(Hard)"）
    ready: bool = False
    locale: str = "zh"  # 玩家语言（zh/en）：握手 ?locale= → join → 本位 GameConfig.locale

    def clear(self) -> None:
        """清空 slot，还原为 open。"""
        self.kind = "open"
        self.player_id = ""
        self.name = ""
        self.ready = False
        self.locale = "zh"


class Room:
    """房间状态机：管理 slot 分配 + 状态流转。

    状态转移：
      lobby → start() → starting → mark_in_game() → in_game
      in_game → mark_ended() → lobby（slot 保留，ready 清零）
    """

    def __init__(
        self,
        map_name: str = "DaybreakLE",
        max_slots: int = 4,
        realtime: bool = True,
    ) -> None:
        self.map_name = map_name
        self.state: RoomState = "lobby"
        self.slots: list[Slot] = [Slot(index=i) for i in range(max_slots)]
        self.host_player_id: str = ""
        self.match_id: str = ""
        # M4：realtime 是进房间配置，房主可在 lobby 阶段切换（set_realtime）
        self.realtime: bool = realtime

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def slot_of(self, player_id: str) -> Slot | None:
        """返回该玩家占用的 slot，不在则 None。"""
        for s in self.slots:
            if s.kind == "bot" and s.player_id == player_id:
                return s
        return None

    def bot_slots(self) -> list[Slot]:
        """所有真人玩家 slot。"""
        return [s for s in self.slots if s.kind == "bot"]

    def filled_slots(self) -> list[Slot]:
        """所有已占用 slot（真人 + 电脑）。"""
        return [s for s in self.slots if s.kind in ("bot", "computer")]

    # ------------------------------------------------------------------ #
    # 内部校验工具
    # ------------------------------------------------------------------ #

    def _require_lobby(self) -> None:
        """确保当前在 lobby 态，否则抛错。"""
        if self.state != "lobby":
            raise RoomError("对局进行中，不能改房间设置", key="room.err.no_change_in_game")

    def _require_host(self, player_id: str) -> None:
        """确保操作者是房主，否则抛错。"""
        if player_id != self.host_player_id:
            raise RoomError("只有房主能做这个操作", key="room.err.host_only")

    # ------------------------------------------------------------------ #
    # 玩家进出操作（全部要求 lobby 态）
    # ------------------------------------------------------------------ #

    def join(self, player_id: str, name: str, locale: str = "zh") -> Slot:
        """玩家进入房间，返回分配的 slot。

        同 pid 重连（手机刷新 / 对局中断线重连）：任何状态都允许，只更新显示名，不占新 slot（幂等）。
        新 pid 加入：必须在 lobby 态，否则抛 RoomError（"对局进行中"）。
        locale：玩家语言（zh/en），存到 slot，开局时写进本位 GameConfig.locale。
        """
        existing = self.slot_of(player_id)
        if existing is not None:
            # 重连：任何状态均可，只更新显示名 + 语言
            existing.name = name
            existing.locale = locale
            return existing
        # 新玩家加入：必须 lobby 态。
        # 就地判断而非复用 _require_lobby() —— 后者被 set_race/set_team/set_ready/
        # add_computer 等共用，改它会让设置类操作也出现"无法加入"文案（设计文档 #4）。
        if self.state != "lobby":
            raise RoomError("对局进行中，无法加入", key="room.err.cannot_join_in_game")
        # 找第一个空位
        for s in self.slots:
            if s.kind == "open":
                s.kind = "bot"
                s.player_id = player_id
                s.name = name
                s.ready = False
                s.locale = locale
                # 第一个加入的玩家成为房主
                if not self.host_player_id:
                    self.host_player_id = player_id
                return s
        raise RoomError("房间满了", key="room.err.full")

    def leave(self, player_id: str) -> None:
        """玩家离开房间，释放 slot；房主离开则转移房主权。"""
        s = self.slot_of(player_id)
        if s is None:
            return
        s.clear()
        if player_id == self.host_player_id:
            # 把房主权转给剩余第一个真人玩家
            remaining = self.bot_slots()
            self.host_player_id = remaining[0].player_id if remaining else ""

    # ------------------------------------------------------------------ #
    # slot 属性设置（全部要求 lobby 态）
    # ------------------------------------------------------------------ #

    def set_race(self, player_id: str, race: str) -> None:
        """设置种族（仅 lobby 态）。"""
        self._require_lobby()
        if race not in ("Protoss", "Terran", "Zerg", "Random"):
            raise RoomError(f"未知种族 {race}", key="room.err.unknown_race", race=race)
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上", key="room.err.not_in_slot")
        s.race = race

    def set_team(self, player_id: str, team: int) -> None:
        """设置队伍（仅 lobby 态）。"""
        self._require_lobby()
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上", key="room.err.not_in_slot")
        s.team = int(team)

    def set_ready(self, player_id: str, ready: bool) -> None:
        """设置准备状态（仅 lobby 态）。"""
        self._require_lobby()
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上", key="room.err.not_in_slot")
        s.ready = bool(ready)

    def add_computer(
        self, requester: str, race: str, difficulty: str, index: int | None = None
    ) -> Slot:
        """房主添加内置 AI（仅 lobby 态，仅房主）。

        index：指定空位下标（玩家点某个空位加电脑，2026-06-12 用户反馈 #3）；
        None = 第一个空位。

        引擎限制（2026-06-12 spike 实测）：SC2 多 agent 局仅支持纯 1v1
        （create_game 报 InvalidPlayerSetup: Only 1v1 is supported when using
        multiple agents）→ 已有 2 个真人时不允许加电脑。
        """
        self._require_lobby()
        self._require_host(requester)
        if len(self.bot_slots()) >= 2:
            raise RoomError(
                "引擎限制：双真人局不支持加电脑（SC2 多 agent 仅 1v1）",
                key="room.err.no_computer_2human",
            )

        def _fill(s: Slot) -> Slot:
            s.kind = "computer"
            s.race = race
            s.difficulty = difficulty
            s.name = f"电脑({difficulty})"
            s.ready = True  # 电脑默认 ready
            return s

        if index is not None:
            if not 0 <= index < len(self.slots):
                raise RoomError("slot 不存在", key="room.err.no_such_slot")
            if self.slots[index].kind != "open":
                raise RoomError("该位置不是空位", key="room.err.slot_not_open")
            return _fill(self.slots[index])
        for s in self.slots:
            if s.kind == "open":
                return _fill(s)
        raise RoomError("房间满了", key="room.err.full")

    def take_slot(self, player_id: str, index: int) -> None:
        """玩家换到指定空位（仅 lobby 态；2026-06-12 用户反馈 #4 自由换位）。

        保留 ready 状态与种族（换位不重置选择）；房主换位不影响房主身份。
        """
        self._require_lobby()
        if not 0 <= index < len(self.slots):
            raise RoomError("slot 不存在", key="room.err.no_such_slot")
        cur = self.slot_of(player_id)
        if cur is None:
            raise RoomError("你不在任何 slot 上", key="room.err.not_in_slot")
        if cur.index == index:
            return  # 点自己当前位 = noop（在空位校验之前判，自己的位不是 open）
        target = self.slots[index]
        if target.kind != "open":
            raise RoomError("该位置不是空位", key="room.err.slot_not_open")
        # 搬家：复制玩家属性到目标位，原位清空
        target.kind = "bot"
        target.player_id = cur.player_id
        target.name = cur.name
        target.race = cur.race
        target.team = cur.team
        target.ready = cur.ready
        cur.clear()

    def remove_slot(self, requester: str, index: int) -> None:
        """房主移除 computer slot 或踢玩家（仅 lobby 态，仅房主）。"""
        self._require_lobby()
        self._require_host(requester)
        if not 0 <= index < len(self.slots):
            raise RoomError("slot 不存在", key="room.err.no_such_slot")
        s = self.slots[index]
        if s.player_id == requester:
            raise RoomError("不能踢自己", key="room.err.cant_kick_self")
        s.clear()

    # ------------------------------------------------------------------ #
    # M4: realtime 配置（仅 lobby 态，仅房主）
    # ------------------------------------------------------------------ #

    def set_realtime(self, requester: str, realtime: bool) -> None:
        """房主切换 realtime 模式（仅 lobby 态）。

        realtime=True：1x 实时速度（玩家看画面，默认）。
        realtime=False：fast 模式（selftest / 调试用）。
        """
        self._require_lobby()
        self._require_host(requester)
        self.realtime = bool(realtime)

    # ------------------------------------------------------------------ #
    # 开局 / 状态推进
    # ------------------------------------------------------------------ #

    def start(self, requester: str) -> None:
        """房主发起开局，校验通过后状态 → starting，生成 match_id。

        校验规则：
        1. 必须在 lobby 态
        2. 必须是房主
        3. 至少 2 个参与者（真人 + 电脑合计）
        4. 至少 1 个真人玩家
        5. 所有真人玩家已 ready
        6. S1：真人玩家数 ≤ 2（3+ 实测后放开）
        """
        self._require_lobby()
        self._require_host(requester)
        filled = self.filled_slots()
        if len(filled) < 2:
            raise RoomError("至少要 2 个参与者", key="room.err.need_2_participants")
        humans = self.bot_slots()
        if not humans:
            raise RoomError("至少要 1 个玩家", key="room.err.need_1_player")
        # S1：暂不支持 3+ 真人（实测后放开）
        if len(humans) > 2:
            raise RoomError("3+ 真人玩家暂未支持(实测后放开)", key="room.err.max_2_humans")
        # 引擎限制（2026-06-12 spike）：多 agent 局仅纯 1v1，双真人不能带电脑
        # （add_computer 已拦"先双真人后加电脑"，这里拦"先加电脑后进第二个真人"）
        computers = [s for s in self.slots if s.kind == "computer"]
        if len(humans) == 2 and computers:
            raise RoomError(
                "引擎限制：双真人局不支持加电脑，请先移除电脑位",
                key="room.err.remove_computer_first",
            )
        # 房主点开始=已就绪，无需单独 set_ready；排除房主后再检查其他玩家
        not_ready = [s.name for s in humans if not s.ready and s.player_id != self.host_player_id]
        if not_ready:
            raise RoomError(
                f"还有玩家未准备：{'、'.join(not_ready)}",
                key="room.err.not_ready",
                names="、".join(not_ready),
            )
        self.state = "starting"
        self.match_id = f"match_{time.strftime('%Y%m%d_%H%M%S')}"

    def mark_in_game(self) -> None:
        """MatchOrchestrator 确认所有子进程进入 playing → 切 in_game。"""
        self.state = "in_game"

    def mark_ended(self) -> None:
        """对局结束，回到 lobby：slot 保留，ready 清零（再来一局少点一次）。"""
        self.state = "lobby"
        self.match_id = ""
        for s in self.bot_slots():
            s.ready = False

    # ------------------------------------------------------------------ #
    # 序列化（room_state 下行帧）
    # ------------------------------------------------------------------ #

    def to_frame(self) -> dict[str, Any]:
        """生成 room_state 下行帧（PWA 消费）。

        M4：含 realtime 字段，供 PWA 显示当前游戏速度设置。
        """
        return {
            "type": "room_state",
            "state": self.state,
            "map": self.map_name,
            "host_player_id": self.host_player_id,
            "match_id": self.match_id,
            "realtime": self.realtime,
            "slots": [
                {
                    "index": s.index,
                    "kind": s.kind,
                    "team": s.team,
                    "race": s.race,
                    "difficulty": s.difficulty,
                    "player_id": s.player_id,
                    "name": s.name,
                    "ready": s.ready,
                }
                for s in self.slots
            ],
        }
