# ADR 0006：基础 bot 通用 auto-pilot

日期：2026-05-15
状态：已采纳

## 背景

M1.6 真实启动 SC2 端到端时,用户(资深 SC2 玩家)反馈基础 bot「太弱」——闲置农民
不采矿、opening build 跑完后 bot 躺平。根因:`_VibeCraftBot` 只接了 ares 的
opening `build_order_runner`,**没接 ares 的 macro behaviors**(Mining / 出兵 /
扩张等),所以 opening steps 之外没有任何自动运营。

设计文档 §6「基础 bot 能力标定」明确要求:经济/后勤 STRONG、建造/生产 STRONG、
玩家沉默时 auto-pilot。当前是实现缺口,不是设计问题。

## 决策

`_VibeCraftBot.on_step` 每 tick `register_behavior()` 一套 ares macro behaviors
(`behavior_executioner` 每 `_after_step` 清空注册列表,故必须每 tick 重注册)。
**两阶段**,用 `build_order_runner.build_completed` 分界:

- **阶段一(opening 未跑完)**:只跑不和 build runner 抢资源的 `Mining` +
  `AutoSupply`。
- **阶段二(build_completed)**:追加会主动造建筑 / 出兵的 `BuildWorkers` /
  `GasBuildingController` / `ExpansionController` / `ProductionController` /
  `SpawnController`(opening 期间它们会和 build runner 的建筑/出兵节奏冲突)。

通用军队组合(`SpawnController` / `ProductionController` 共用):不朽 0.25 /
追猎 0.55 / 叉子 0.20。

## 范围标定

目标:**无玩家干预时 ≈ 普通电脑(Medium AI)级别**(用户明确接受这个标定)。
「按 midgame/lategame 剧本自动转」**不在此** —— 那是 M2 的核心(ADR 0003 已把
midgame/lategame 的 ares 接入留给 M2)。本 ADR 是「通用兜底」,让 bot 不躺平。

## role 隔离(关键约束)

auto-pilot 的所有 behavior **不碰** `UnitRole.CONTROL_GROUP_ONE`(vibecraft 把它
当「被玩家语音接管的特种兵」标记)。调研结论:所有造建筑/扩张/补气类 behavior 选
worker 都走 `mediator.select_worker`(只取 `UnitRole.GATHERING`);`SpawnController`
/ `ProductionController` 只操作生产建筑、不按角色抓 army 单位;ares 的 `catch_unit`
自动派角色只对 worker 生效,auto-pilot 出的兵处于无角色 idle 状态。**全部天然隔离**。

## 实现

`src/vibecraft/bot/ares_adapter.py` `make_bot_class` 内:
- import 块加 `ares.behaviors.macro` 的 7 个 behavior + `sc2.ids.unit_typeid`
- `generic_army` 常量 + `target_worker_count` / `target_base_count`
- `_VibeCraftBot.on_step` 调 `self._register_auto_pilot()`
- 新增 `_register_auto_pilot()` 方法(两阶段注册)

完整方案 + 4 个 spike 验证点见 `docs/plans/2026-05-15-auto-pilot.md`。

## 待真实验证(端到端 smoke)

- ⚠️ A:`super().on_step()` 内部异常不会导致 auto-pilot 永远不注册
- ⚠️ B:Protoss WARPGATE 出兵走 `request_warp_in`,折跃门正常折跃
- ⚠️ C:玩家中后期语音切新 long opening 时,`build_completed` 仍 True 的短暂冲突
- ⚠️ D:跑起来后 `CONTROL_GROUP_ONE` 单位数不被 auto-pilot 影响
- 整体:opening 跑完后 3-4 基地饱和采矿、农民补到 ~66、supply 不卡、有稳定兵流
