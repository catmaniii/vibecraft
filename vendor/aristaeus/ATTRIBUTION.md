# Aristaeus — Attribution

- **Upstream URL**: https://github.com/august-k/Aristaeus
- **Cloned commit**: `d3d8928b0901ae4aeece261e237dd62a289185b1`
- **Clone date**: 2026-05-16
- **License**: MIT（见同目录 LICENSE 文件）

## 用途

VoiceCraft 神族 bot (`_VoiceCraftProtossBot`) 继承 Aristaeus 的 `MyBot`，
复用其 cannon-rush opening + combat / production manager 框架，
在此基础上叠加 voicecraft 的语音指挥层（Director / Facade / Directive Board）。

## 修改说明

Aristaeus 锁定 ares-sc2 1.15.1 而 voicecraft venv 装的是 3.7.2。
两个主版本之间有 import 路径漂移(无语义变化),已对 vendor 代码做最小机械 patch:

1. **`from ares.cython_extensions.*` → `from cython_extensions.*`**(6 个文件 6 处)
   - ares 3.x 把 `cython_extensions` 拆成顶层独立包(`cython-extensions-sc2`),不再嵌套
   - 影响文件:`bot/combat/oracle_harass.py` / `bot/combat/oracle_scout.py` /
     `bot/combat/tempest_offensive.py` / `bot/managers/cannon_rush_manager.py` /
     `bot/managers/oracle_manager.py` / `bot/managers/production_manager.py`

2. **`CombatBehavior` → `CombatIndividualBehavior`**(`bot/behaviors/oracle_kite_forward.py` 2 处)
   - ares 3.x 把 `CombatBehavior` 重命名拆到子模块 `ares.behaviors.combat.individual`
   - 改 1 行 import + 1 行 类继承

`scripts/sync_vendor.ps1 -Repo aristaeus` 重新同步 upstream 时,需要重新应用这两组 patch。
后续 ares 3.x 升到 4.x 再出新漂移时,看 `docs/plans/2026-05-16-tri-race-bots.md` §S2 spike 结论。
