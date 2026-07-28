# ADR 0007：async move_camera 用 fire-and-forget create_task 包装

| 字段 | 值 |
|---|---|
| 日期 | 2026-05-15 |
| 状态 | **已撤回（superseded by [ADR 0008](0008-camera-staged-drain.md)）** |
| 触发 | minimap 里程碑 spike S1：发现 facade 的 `move_camera` 同步调 async 协程等于没调 |

> **撤回原因**：fire-and-forget 在真实 SC2 上让客户端立即崩。python-sc2 的
> `Protocol.__request` 是 send_bytes 后立刻 receive_bytes,**无 request id**,
> 隐含"单 socket 一发一收串行"假设。`asyncio.create_task` 让 `move_camera`
> 与 bot 主 step 并发往同一个 ws 写 bytes,帧交织导致 SC2 协议解析失败客户端崩溃。
> 正确修法见 ADR 0008(staged + on_step drain)。

## 背景

`bot.client.move_camera`（BurnySc2 `sc2/client.py:495`）是 async 协程：

```python
async def move_camera(self, position: Union[Unit, Units, Point2, Point3]):
    await self._execute(action=sc_pb.RequestAction(actions=[...]))
```

原 facade 的 `move_camera` 同步姿势调它：

```python
def move_camera(self, point: tuple[float, float]) -> None:
    self.bot.client.move_camera(Point2(point))  # ← 只产生 coroutine 对象，未 await
```

效果：coroutine 被立即 GC，**没有发任何 SC2 请求**。运行时会产生
`RuntimeWarning: coroutine 'BotAIInternal.move_camera' was never awaited`，
但被 filterwarnings 淹没，很难发现。

## 方案对比

| 方案 | 代价 | 结论 |
|---|---|---|
| A. `asyncio.create_task` fire-and-forget | facade 协议保持同步；调用方（director._dispatch_view / on_step view_move 分支）不需要 async | ✓ **采用** |
| B. 把 facade.move_camera 改成 async，一路传播 | director._dispatch_view 是同步函数；改成 async 会蔓延到 Director.on_tick → BoardEvent 处理链，改动面大 | ✗ |
| C. 用 `loop.run_until_complete` 同步等 | on_step 本身在 event loop 里，run_until_complete 会抛 RuntimeError | ✗ |

## 采用方案 A 的细节

```python
def move_camera(self, point: tuple[float, float]) -> None:
    import asyncio
    from sc2.position import Point2
    coro = self.bot.client.move_camera(Point2(point))
    task = asyncio.create_task(coro, name=f"move_camera-{point}")
    task.add_done_callback(_log_move_camera_done)
```

- `on_step` 本身在 event loop 里，`create_task` 拿得到 running loop。
- fire-and-forget 语义：相机移动是单向命令，不需要等返回值；
  下一帧 on_step 跑前 SC2 已处理完上一帧的 move_camera。
- done callback `_log_move_camera_done` 捕获异常并 log，不静默丢弃。
- `follow_unit` 同理修复（同样的同步调 async 问题）。

## 影响

- view_move 链路（minimap 拖拽 → 手机 → WS → down_q → on_step → facade.move_camera → SC2）正式打通。
- director._dispatch_view（LLM 语音控制视野）也跟着 work（不需要额外改）。
- 单测：`test_minimap.py::TestSharpyFacadeMoveCameraAsync` 验证 create_task 被 schedule。
