# bio_stim(3矿5BB)产能效率调查 — 已解决(2026-06-18)

> 用户报:3矿5bb 开局"开矿过激进、没几个兵就开矿"。深挖后发现是产能/科技级联病。
> 本文档先记早期(错误)推断,再记 telemetry 实证出的真根因 + 已落地的修复。**已解决**:
> build_acceptance bio_stim vs VeryHard/VeryEasy 从 **5/14 → 12/14**。对应任务 #537。

## 一、被推翻的早期推断(留作教训)
最初看 `attack_moveout=181s` 就下结论"出门太早 → 送兵 → 崩"。用户反问"出门前产能拉满了吗?
拉满那波采矿出门是有局部优势的;没拉满才显得太早"。逼我查 telemetry。
**结论:这是错的。** `start_attack_power=18` 是 red herring —— sharpy 要 `our_power ≥ 18×1.2≈21.6`
(约 15 枪兵)才出门,而出门前 army=6,根本触发不了进攻。死因不是自杀,是**科技/产能级联**。

## 二、telemetry 实证的真根因(VeryHard 3 run 一致)
出门那刻(~181s):1 兵营、5 枪兵、气 flood 840。然后:
- **TechLab 拖到 580s、stim 到 749s(9.6min!)、掠夺者全程 0、气 flood 2000**。
- 3-9min 全程是**无 stim、无掠夺者的纯枪兵** → 2/3 局被 VeryHard 中期(~6min)碾死。

根因(独立缠绕的三条):
1. **TechLab 永不挂**:`BuildAddon` 只在 `.ready.idle` 兵营挂挂件(`vendor/.../build_addon.py:46`)。
   Marine(priority) 排在产线前每帧把所有兵营塞满枪兵 → 兵营永不空闲 → TechLab 挂不上,
   直到 ~650s 钱 flood 兵营才偶尔空闲挂上 → stim 749s。
2. **掠夺者/医疗船恒 0 + 气 flood**:同理 Marine 把 TechLab 兵营也塞满枪兵 → 掠夺者抢不到
   档期 → 0 掠夺者 → gas(掠夺者/医疗船的消耗口)无出口 → 堆到 2000。
3. **早期矿荒**:BB1-ready 同帧并发 Expand2(400矿)+Factory+gas2+gas3,400 的 CC 把
   BB2/TechLab 的矿抢光 → 兵营卡 1 个到 312s。

## 三、修复(全部落在 `bio_stim.py` 的 plan 编排,无新执行器)
1. **2 个 BB-TechLab 挂件前置到 Marine 产线前** → 兵营产完一发空闲那帧 BuildAddon 先抢到手挂上。
   stim 749s→**247s**。
2. **全部产兵(掠夺者/医疗船/枪兵)下移到所有建筑步之后**(建筑先吃矿成型再产兵;附带让
   TechLab/Reactor 在建筑期天然空闲的兵营上挂上),掠夺者/医疗船排枪兵前抢专属建筑档期。
   掠夺者 0→**8-15**、医疗船 0→**6-7**、气不再 flood。
   - 关键教训:任何重矿单位(掠夺者 100 矿)产线排建筑步**前**都会抽干矿 → 二矿/BB3-5 饿死、
     经济崩(实测 2 兵营 0 二矿早亡)。产兵必须在建筑后。
3. **Expand2 推到 BB2-ready、BB3 与开矿解耦(BB2-ready)、gas3 推到 Factory-exists** → 矿荒解除,
   5 兵营 330s 稳定。
4. **Starport 紧跟 Factory、工程湾改 Factory-exists 触发并提到 BB4/5 前** → Starport 422s→**243s**、
   +1 攻击 487s→**438s**(二者原排 BB3-5/三矿后抢不到 SCV/矿)。

## 四、二矿/三矿时机(用户 2026-06-18 纠偏)
初版我把 Expand2 推到 BB2-ready(二矿 ~250s)以缓解矿荒,被用户纠正:"二矿可以早点,三矿不宜
太早,二矿延后不是我的需求"——#537 投诉的是**三矿**太早,不是二矿。改：
- **二矿回到 BB1-ready**(CC2 ~3:10,command_center_2 达标)。早 CC2 会让 SCV 第 2 波(CC2-good
  爆农 44)抢 BB2-5 的矿 → 兵营卡 1 个;故**把爬 44 农推迟到 3 兵营齐**才放(兵营优先于第 2 波农民)。
- **三矿改"5 兵营齐 + combat supply ≥ 6"才开**(放高优先级建筑块,gate 一开就建),CC3 ~6:30-6:55
  落地。用户三选一拍板"折中(攒一小股兵)";试过 ≥10/≥14(三矿拖到 7:40 伤经济)、高优先级裸 army 门
  (抢 BB5 SCV 只 2 兵营)均不取。`command_center_3` spec `at` 同步后移到 6:40。
- 关键实测:army 在 5:00-6:00 间从 6 飙到 18,故门槛 6/10/14 对 CC3 落地时间影响很小;真正控制
  CC3 早晚的是"BB5 何时建好"——SCV 不抢矿后 BB5 ~310s,CC3 紧跟 ~400s。

## 五、结果 + 剩余项
- build_acceptance **5/14 → 13-14/14**(VeryEasy 13/14;VeryHard 13-14/14 有战损方差)。stim ~4:20、
  完整 MMM、5 兵营 2 科技 3 双倍、攻防 +1、二矿 ~3:10 / 三矿 ~6:40(有军队),原"2/3 局 6min 暴毙"
  消失,survive 到 9min+ 带 60 农民。
- 剩余偶发未过项(VeryHard 中后期战损噪声,非结构问题,不再追以免过拟合):`marine_24`(510s 快照
  恰逢出门战损时偏低)/`pressure_reach`。
- 后劲(late-game 产能)归 opening_completed → sustain doctrine 接管,不在本 plan 职责内。

## 五、方法论教训(已存 memory feedback_confirm_production_via_telemetry)
下"出门太早/产能不足"这类时机结论前,先用 telemetry 实证产能/军队/钱(prod_util、各建筑数
随时间、gas/min 是否飘),别凭单个 `attack_moveout` 数字推断。bot 中期就死时 build_acceptance
中后期 check 全被"死亡"污染,要先让 bot 活下来才测得准。
