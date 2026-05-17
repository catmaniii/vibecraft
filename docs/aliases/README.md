# `docs/aliases/` —— SC2 各种族别名 + 通用术语

VibeCraft LLM 解析的中央别名表。

| 文件 | 内容 | 状态 |
|---|---|---|
| `protoss.yaml` | 神族 building/unit/upgrade canonical id → aliases | ✅ MVP (v0.1) 启用 |
| `terran.yaml` | 人族同上 | 📝 占位 skeleton（v1.5）|
| `zerg.yaml` | 虫族同上 | 📝 占位 skeleton（v1.5）|
| `system.yaml` | **跨种族**通用术语（地图位置 / 时钟方位 / 战术概念 / 经济概念 / 军事概念 / 情报视野）| 📝 已写,**加载逻辑未接入**(待 `build_glossary` 抽进 prompt) |

## yaml 格式

每个 yaml 含三组：`buildings` / `units` / `upgrades`。每条 entry：

```yaml
canonical_id:                   # 内部统一 id（与 SC2 unit type name 对齐,代码引用,不能改）
  default_display: "BE"         # PWA / snapshot / 日志字段默认显示
  aliases: [BE, PY, 水晶, Pylon]  # 玩家所有可能说法（含 hotkey 串 / 中文 / 英文）
  hotkey: "B+E"                  # 仅信息,不参与匹配。游戏内键位
```

## 编辑指南

### 加新别名

找到目标 canonical_id → 在 `aliases` 列表追加一条 → 保存。下次 service
重启自动重读；eval / 单测立即生效（重跑 pytest 即可）。

例：让玩家能用 "炸蝗虫" 触发 Disruptor：

```yaml
# protoss.yaml
units:
  Disruptor:
    default_display: "干扰者"
    aliases: [干扰者, Disruptor, 分裂球, 炸蝗虫]   # ← 加这条
```

### 加新单位/建筑/升级

抄相邻 entry 改字段。**canonical_id 必须 = SC2 unit type name**（python-sc2
`sc2.ids.UnitTypeId` enum 字面值）。

### 改 default_display

会影响 PWA 卡片显示 + snapshot 字段 + LLM prompt 别名表 + Director 的 `_UNIT_ZH`
查表（见 `src/vibecraft/bot/director.py`）。Display 改了不破代码，但玩家眼里
界面会变。

### ⚠️ 不能做的事

- **不能改 canonical_id**：代码 + strategies/*.yaml + 单测都引用
- **不能在不同 canonical 之间用相同 alias**：LLM 会歧义，verb 消歧（造/出/研）
  也救不了 building vs unit 同名（如 `球` 既可指 Disruptor 也可指 Archon
  `白球`）
- **不能漏 `default_display` 或 `hotkey` 字段**：load 会 schema 错

## 建筑 hotkey 真值表（Liquipedia Standard 布局）

| 建筑 | hotkey | 串 |
|---|---|---|
| Nexus | B+N | BN |
| Pylon | B+E | BE |
| Assimilator | B+A | BA |
| Gateway | B+G | BG |
| Forge | B+F | BF |
| CyberneticsCore | B+Y | BY |
| PhotonCannon | B+C | BC |
| ShieldBattery | B+B | BB |
| RoboticsFacility | V+R | VR |
| RoboticsBay | V+B | VB |
| Stargate | V+S | VS |
| TwilightCouncil | V+C | VC |
| TemplarArchives | V+T | VT |
| DarkShrine | V+D | VD |
| FleetBeacon | V+F | VF |

注：Probe 建造菜单分两层 — **B (Build basic)** 进基础，**V (Advanced Build)** 进高级。

虫族 / 人族 hotkey 启用时按同样规则从 Liquipedia 查。

## 加载链路

```
docs/aliases/protoss.yaml
  ↓ StrategyLibrary.from_directories(strategies_dir, aliases_path)
  ↓ AliasTable.from_yaml() 解析 → dict[canonical → entry] + 反向 dict[alias → canonical]
  ↓ build_system_prompt(aliases) 拼进 LLM prompt 第 1 段
```

源文件：`src/vibecraft/strategy/aliases.py` `AliasTable`。

## 版本

- 2026-05-17：初版,从 `aliases/protoss.yaml` 移过来 + hotkey 修正
  (V 系列原写 B+ 全改 V+) + display 用真 hotkey 串(BE/BA/BY/BB/VR/VB/VS/VC/VT/VD/VF)
  + 旧自创简称(PY/VC/BG/BF/BC/VR/VD 等)作 alias 保留兼容。
- 虫族 / 人族占位 yaml 新建,M5+ 启用。
