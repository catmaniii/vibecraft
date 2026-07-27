# ADR 0002：GameProcess 用 multiprocessing spawn 方案

| 字段 | 值 |
|---|---|
| 状态 | 已接受 |
| 日期 | 2026-05-14 |
| 里程碑 | M1.2 SC2 子进程生命周期管理 |

---

## 背景

`run_game()` 是同步阻塞调用（M0c smoke 实测：SC2 冷启动到进对局约 5-6s，整局运行几十秒到几分钟）。bot service 的 WS server 跑在 asyncio event loop，不能让 `run_game()` 直接在 loop 里跑。

设计预研列出三个候选方案：

| 方案 | 机制 | 优 | 劣 |
|---|---|---|---|
| A 独立线程 | `threading.Thread` 跑 `run_game()` | 不需序列化，bot 对象直接共享；实现简单 | 线程没法干净 kill；SC2 死锁时线程卡住，只能整进程退 |
| B 独立进程 | `multiprocessing` spawn 子进程 | 隔离彻底，SC2 崩 / 卡不波及 WS server；可 `terminate()` 强杀 | Windows 用 spawn，参数必须 picklable |
| C asyncio executor | `loop.run_in_executor` | 比 A 少手动管理 | 本质还是线程，劣势同 A |

---

## Spike 结论

**方案 B（独立进程）可行，已确认。**

关键验证：
1. `GameConfig` 全是基本类型（`str`, `bool`, `int`），Python `pickle` 序列化无问题（单测 `TestGameConfig.test_picklable` 验证）
2. 父进程不传 bot 对象 —— 只传 `GameConfig`；子进程在自己的进程空间内执行 `import ares` + 构造 bot。这样绕开了 `AresBot` 不可 pickle 的问题
3. `multiprocessing.Queue`（上行 / 下行各一个）是进程安全的，两端都能无锁读写

---

## 决策

**选择方案 B（独立进程 + spawn）**，理由：

1. **进程隔离**：SC2 客户端崩 / 死锁时，父进程（WS server）不受影响，仍能向手机推 `game_status{sc2: "crashed"}`
2. **可强制善后**：`proc.terminate()` / `proc.kill()` 可靠杀掉子进程；线程方案做不到
3. **exitcode 兜底**：即使子进程来不及推 crash 消息（比如 OOM 被系统杀），父进程 `proc.exitcode != 0` 仍可判定 crashed
4. **Windows spawn 约束已规避**：bot 对象不跨进程传递，只传 picklable config

---

## 实现要点

### 进程间通信
- 上行队列（`up_q`）：子进程 → 父进程，推 `{"sc2": ..., "bot": ..., "detail": ...}` dict
- 下行队列（`down_q`）：父进程 → 子进程，推 command dict（M1.4+ 激活）
- asyncio 侧用 `loop.run_in_executor(None, q.get(timeout=1))` 桥接阻塞 `Queue.get()`，不阻塞 event loop

### 阶段映射
子进程 bot 回调 → 上行队列消息 → `game_status` 下行帧：

```
spawn                    → sc2: "launching", bot: "idle"
import sc2 + maps.get() → 失败则 sc2: "crashed"
run_game() 开始          → sc2: "launching", bot: "running"
on_start()               → sc2: "in_game", bot: "running"  → sc2: "playing"
on_end()                 → sc2: "ended", bot: "idle"
run_game() 抛异常        → sc2: "crashed", bot: "error"
proc.exitcode != 0 (兜底)→ sc2: "crashed", bot: "error"
```

### watchdog
`status_events()` 在阻塞等 `Queue.get(timeout=1)` 循环里每秒检查一次 `proc.is_alive()`。子进程非正常退出会被父进程轮询到，不需要额外 watchdog task。

---

## 未决 / 留给后续里程碑

- **M1.4+**：下行队列激活（`leave` / `command` 传进子进程）
- **M1.5**：`_build_bot_class` 里的 stub `_M12Bot` 替换为真正的 `VibeCraftBot`
- **watchdog 超时**：`_LAUNCH_TIMEOUT = 120s` 是保守值，M0c 实测 5-6s；未来可加 launching 超时判定
