# ADR 0008：camera 操作暂存 + on_step 末尾串行 drain

| 字段 | 值 |
|---|---|
| 日期 | 2026-05-15 |
| 状态 | 已采用 |
| 触发 | 真实 SC2 端到端:用户点小地图后 SC2 客户端立即崩,日志在 view_move 后戛然而止 |
| Supersedes | [ADR 0007](0007-async-move-camera-fire-and-forget.md) |

## 背景

ADR 0007 把 `_AresFacade.move_camera` 改成 `asyncio.create_task(self.bot.client.move_camera(...))`
fire-and-forget。代码层面看像对的——`bot.client.move_camera` 是 async,被 schedule 到 event
loop 就一定会跑。但在**真实 SC2 进程**上跑时,一点击小地图,SC2 客户端立即崩溃。

### 根因:python-sc2 的 ws 协议无并发

`sc2.protocol.Protocol.__request`:

```python
async def __request(self, request):
    await self._ws.send_bytes(request.SerializeToString())
    ...
    response_bytes = await self._ws.receive_bytes()
    response.ParseFromString(response_bytes)
    return response
```

**没有 request id**,所有响应都按 send 顺序就近匹配。一旦两个 `_execute` 协程并发跑:

1. bot 主 step:`await send_bytes(StepRequest)`
2. move_camera task:`await send_bytes(CameraAction)` —— 字节流交织进 socket
3. SC2 收到 corrupt protobuf 帧 → 客户端协议错误 → 崩溃 + 进程退出

实测日志:

```
19:40:31 ws_view_move_sent point=[47.87, 37.95]
19:40:31 ws_minimap_sent ts=3.348
[此后无新日志,SC2 进程消失]
```

bot 子进程没死(还在 await 协议响应,挂着),所以也没 traceback 出来。

## 方案

camera 操作改"暂存 + on_step 末尾串行 await":

```python
class _AresFacade:
    def __init__(self, bot):
        self.bot = bot
        self._pending_camera_point: tuple[float, float] | None = None

    def move_camera(self, point):
        # 同步姿势:只暂存,合并多次调用为 latest
        self._pending_camera_point = point

    def follow_unit(self, unit_tag):
        unit = self.bot.units.find_by_tag(unit_tag)
        if unit is not None:
            self._pending_camera_point = (unit.position.x, unit.position.y)

    async def drain_pending_actions(self):
        if self._pending_camera_point is None:
            return
        pt = self._pending_camera_point
        self._pending_camera_point = None
        try:
            await self.bot.client.move_camera(Point2(pt))
        except Exception as exc:
            logger.warning("move_camera_failed point=%s err=%s", pt, exc)


class _VoiceCraftBot(AresBot):
    async def on_step(self, iteration):
        ...
        if self.director is not None:
            self.director.on_tick(now=float(self.time))
        # step 末尾串行 await
        if self.facade is not None:
            await self.facade.drain_pending_actions()
```

### 关键性质

- **facade 协议保持同步签名**:`move_camera`/`follow_unit` 调用方
  (`director._dispatch_view`、`on_step` 的 view_move 分支)不需要改 async
- **真正的 await 在 step 链内**:`drain_pending_actions` 在 `super().on_step()`
  之后被 await,跟 bot 主 step 串行,不与 SC2 协议请求 race
- **节流合并**:多次 `move_camera` 调用只 keep latest;手机端拖动节流后通常
  几十 ms 一次,1 tick 内最多发 1 次 camera 请求,不会堆积
- **异常吞掉**:相机失败不该让整个 bot 挂(SC2 已经在状态不一致时,继续打没意义)

## 影响

- ADR 0007 推荐的 `_log_move_camera_done` callback 也一并删掉
- 单测从 `TestAresFacadeMoveCameraAsync`(验 create_task)改成 `TestAresFacadeMoveCameraStaged`:
  - `test_move_camera_stages_point_not_immediate_call`:调 `move_camera` 后 `client.move_camera` **未**被调
  - `test_drain_pending_actions_awaits_move_camera`:`drain` 后 `client.move_camera` 被 await 一次,`_pending_camera_point` 被清
  - `test_on_step_view_move_stages_then_drains`:`on_step` 一次 tick 内既调 `facade.move_camera` 又调 `facade.drain_pending_actions`
- M2+ 加更多 SC2 协议调用(`set_build_location_override`/`execute_unit_action`)时,
  同样要走 "暂存 + on_step drain" 模式,**不能 fire-and-forget**

## 教训

调 async 库的时候,**默认假设它不能并发**,除非文档明示支持。python-sc2 文档没说,
但源码里 `__request` 是经典的"一发一收"模式,这种代码并发跑就是炸。
"在 async 上下文 await 协程"和"用 create_task 调度"语义完全不同,后者引入并发。
