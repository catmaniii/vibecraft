# Build 执行质量评审清单

> 评审"一个 SC2 build 执行到不到位"的标准。配合 `scripts/build_acceptance.py`（timing+防守）、
> `scripts/build_efficiency.py`（经济四维）、手读 `logs/game_*/telemetry.jsonl`（idle/gas/后劲）三件套。
> 与 CLAUDE.md「Build 执行质量自检标准」同源，这里是评审视角的完整清单。

**一句话**：到位 = **资源一直在流动、产能一直在转、该到的 timing 都到、活得下来、且开局完了不摆烂。**

---

## 7 个维度 + 1 条底线

| # | 维度 | 病征 | 信号 / 阈值 | 工具 |
|---|---|---|---|---|
| ① | 农民不闲置 | 早期农民不采矿/不侦查/不建造 | `idle_workers` 早期≈0、持续>0=病；`gas_workers=0`=没采气 | telemetry |
| ② | 农民满采不过饱和 | 主矿没采满 / 农民远超 ideal 空转 | `mineral_workers`≈且不远超 `mineral_ideal`；气满采(每口3) | telemetry |
| ③ | 资源不堆积 | 钱/气囤着没花 | `avg_excess_bank`>500（supply≥180 成长期后不罚） | build_efficiency M1 |
| ④ | 产能利用率高 | 产线建筑/larva 闲着不产 | `prod_util`<0.6=产能空 | build_efficiency M2 |
| ⑤ | 不卡人口/资源 | 有钱有产能却卡人口；资源够却没下单 | `supply_block_time`>15s（已滤<4s 健康 JIT） | build_efficiency M3 |
| ⑥ | 科技链第一时间到位 | A 建好不接 B→C；timing 落后 spec、串中卡资源 | 各 building/upgrade check 在 `at±tol` 内 PASS | build_acceptance |
| ⑦ | 后劲充足（**最易漏**） | opening 完成后摆烂：supply 卡死、钱涨几万、兵种卡 plan 写死的 N | supply 单调涨≥180；核心兵种突破写死上限；钱不堆 5000+ | 手读 telemetry snapshot 序列 |
| ⑧ | **活得下来（底线门）** | 早期防守崩、被一波带走 | VeryHard 局能活到中后期（前 7 条再好，崩了也白搭） | build_acceptance VeryHard |

---

## 评审流程（实际怎么跑）

1. **1 局 VeryEasy + 3 局 VeryHard 混合**：
   - VeryEasy(5-7min)：早期 build 顺序 + 一波打完；但晚期 check 游戏没到就结束 → 只它会漏。
   - VeryHard(10+min, 可能 Tie/Defeat)：中后期 timing + 防守(⑧) + 后劲(⑦)。per-check 取多数票(3 局≥2 PASS)。
   - 命令：
     ```
     scripts/build_acceptance.py <id> --runs 1 --opponent veryeasy
     scripts/build_acceptance.py <id> --runs 3 --parallel 3 --opponent veryhard
     ```
2. **build_efficiency 打分**（读上面那局 telemetry）：`score <telemetry.jsonl>` → ②③④⑤ 四维 + worst_dimension。
3. **手读 telemetry snapshot 序列**：补 ①（早期 idle/gas）+ ⑦（后劲）—— 自动门最弱，必须人眼扫：
   ```python
   recs=[json.loads(l) for l in open('logs/game_<id>/telemetry.jsonl')]
   for s in [r for r in recs if r['kind']=='snapshot'][::30]:
       print(s['t'], s['supply_used'], s['units'], s.get('minerals'), s['economy'])
   ```
4. **任一维度有病 → 调 plan 修回来，别改 spec 数值掩盖** → 重跑确认。

---

## 典型病 → 一眼信号（诊断速查）

| 现象 | 大概率根因 |
|---|---|
| 农民数停在某值不涨 + minerals 一直贴地(~50) | 早期结构投资把矿吃光、暴农被挤后 |
| 虫族 supply 卡死、兵种=`ZergUnit(X,N)` 的 N | `opening_completed` 没触发 → sustain 没接管 → 摆烂 |
| 蜂后 0 个到 4 分钟（虫族） | 没注卵=没 larva=宏观死；结构投资压过蜂后 |
| 钱/气涨到 5000+ 持续 | 没出口：产线不够/兵种卡上限/没扩张 |
| 升级建筑 ready 但攻防/科技链没刷 | tech 链断节，后置没 gate 在前置 ready |
| `attack_moveout` 一直不触发，兵够却龟 | sharpy "经济优势+军队劣势龟防"逻辑(修:`attack_on_advantage=False`+调`start_attack_power`) |

---

## 评审本身的坑

1. **别信主基 HUD 跳动的农民数**：speed mining 每帧 move 农民→计数闪烁，是显示假象。看 telemetry `mineral_workers`。
2. **后劲最易漏**：acceptance 主验早期，opening 完成后摆烂它抓不到 → 必须手读 snapshot 序列。
3. **VeryEasy 单跑会假绿**：晚期 check 游戏结束后才到 → 无 snapshot。必配 VeryHard 长局。
4. **指标变差调 plan，别放宽 spec**：改 spec 数值把红盖绿 = 自欺。
5. **聚合判据掩盖 per-instance 失败**：多基地/多兵种 per-instance 分别看，别看"最好的那个"。
6. **确认两档对手都跑了**：只跑一组就下结论会漏另一档。
