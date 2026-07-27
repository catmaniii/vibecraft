# 科技/施法单位主动技能补全设计(P1+P2)

> 2026-06-18。用户:鬼兵不放 EMP/狙击、女妖被打不隐形,"所有科技兵种好好检查"。
> 三族审计(3 个并行 agent)结论:**vibecraft 走 sharpy 默认 `MicroRules.unit_micros` 表,
> 没注册 micro 的单位 = 主动技能永不触发**。

## 0. 根因(已证实)
- `vendor/sharpy/sharpy/combat/micro_rules.py::load_default_micro` 注册 `unit_micros[UnitTypeId.X] = MicroX()`。
  HT storm 实测能放 = 默认表生效。
- **GHOST / BANSHEE 没注册** → generic_micro,EMP/狙击/隐形永不放。
- **ROACH 的 `MicroRoaches.__init__(self, knowledge)` 是异常签名**(其余 micro 都是无参 `__init__(self)`,
  knowledge 经 `async start(knowledge)` 注入)→ 注册 `MicroRoaches()` 会 TypeError = 它被漏注册的真因。
- 施法 API(已读 micro_infestors/micro_queens 确认):`unit_solve_combat` 返回
  `Action(target_unit_or_pos, queue=False, ability=AbilityId.XXX)`;可用 helper:
  `self.cd_manager.is_ready(tag, ability)`、`self.enemies_near_by`、`self.engaged_power.power`、
  `self.unit_values.power(enemy)`、`self.cache.enemy_in_range/own_in_range`、
  `self.pather.find_weak_influence_ground/air`、`self.move_type`、`unit.energy`、`enemy.has_buff()`。

## 1. 架构决策
- 新 micro 类放 **vendor sharpy**(`combat/terran/`、`combat/protoss/` …),与现有结构一致;
  vibecraft 已直接 vendor-patch 这些 micro(MicroStalkers/HighTemplars),约定成熟。
- **注册**:改 `micro_rules.py::load_default_micro` 加 `unit_micros[...]=...`。
- **vendor patch 合规**:
  - 改既有 upstream 文件(micro_rules / micro_roaches / micro_sentries / micro_vipers / micro_ravens /
    micro_medivacs)→ 加 `# vibecraft:` marker + `docs/sharpy-patches.md` 新节 + 视情进
    `test_sharpy_patch_audit.py::PATCHED_METHODS`。
  - 新文件(micro_ghosts / micro_banshees / micro_cyclones)→ 文件头 `# vibecraft:` 说明是新增,
    sharpy 升级不会覆盖(无 upstream 对应)。
- **施法日志**:每个新/改 micro 真放技能那一刻打 env 门控 greppable 日志
  (`VIBECRAFT_CASTER_TRACE`,如 `CASTERTRACE ghost snipe target=...`)→ 真局自验 grep 断言。

## 2. P1(用户点名 + 真·永不放)

### 2.1 MicroGhosts(新,`combat/terran/micro_ghosts.py`)
优先级:**Snipe > EMP > Cloak(survival)**(攻击技能优先,cloak 兜底保命,避免能量互抢)。
- **狙击 Snipe `EFFECT_GHOSTSNIPE`**(费 50,只打**生物**单位):
  - 门:`cd_manager.is_ready(tag, EFFECT_GHOSTSNIPE)` + `unit.energy >= 50` + 有敌在 ~10 格。
  - 目标优先:敌方**高价值生物**——感染虫/毒蝙/分裂虫/雷兽/BL/女王/高阶圣堂/感染步兵等
    (`unit_values.power(e)` 高 + `e.is_biological` + 非已 snipe 标记)。打分 = power*权重 + health;
    用 `tag % shuffler` 防同帧多鬼锁同一目标(抄 infestor 的去重技巧)。
  - 排除:机械/建筑(snipe 打不到)。
- **EMP `EMP_EMP`**(费 75,AoE 砸位置,削护盾/能量):
  - 门:`is_ready(EMP_EMP)` + `unit.energy >= 75` + 敌群里有**护盾(Protoss)或高能量 caster**。
  - 目标:`cache.enemy_in_range(pos,1.5)` 最密、且含护盾/能量的点。打 position。
- **隐形 Cloak `BEHAVIOR_CLOAKON_GHOST`**(持续耗能):
  - 门:研究了隐形 + `unit.energy` 充足(留 snipe 余量,如 energy 在 50-75 之间没 snipe 目标时)+
    敌near + **本帧没更优的 snipe/EMP 可放** + 未被敌方探测(`detector near` 时 cloak 无意义,跳过)。
  - 保守:cloak 只在"有威胁但无 cast 目标"时保命,不抢攻击技能能量。
- 无 cast 时回 `stay_safe`(鬼兵脆,远离 influence)。

### 2.2 MicroBanshees(新,`combat/terran/micro_banshees.py`)
- **隐形 Cloak `BEHAVIOR_CLOAKON_BANSHEE`**:
  - 门:研究了隐形(`is_ready(BEHAVIOR_CLOAKON_BANSHEE)`)+ `unit.energy > ~30`(留够撑一会)+
    敌near(在战斗/被打)+ 未被探测(附近无敌 detector/turret)+ 未已隐形。
  - 依据用户:"被打了也不会用隐形" → 接敌即隐形保命(对无探测的敌方尤其强)。
  - 其余走 GenericMicro 普攻(banshee 是攻击机,打地面)。
- 用 GenericMicro 基类(要普攻 + 技能),`__init__(self)` 无参。

### 2.3 MicroRoaches 修注册(`micro_roaches.py` + `micro_rules.py`)
- 改 `__init__(self, knowledge)` → `__init__(self)`(`super().__init__()`),knowledge 经 start 注入。
- `micro_rules.py` 加 `unit_micros[UnitTypeId.ROACH] = MicroRoaches()`。
- 现有埋地逻辑(<40% 血钻地、>70% 钻出、`is_ready(BURROWDOWN_ROACH)` 隐含要研究了潜地)保留。
  注:无穿地爪时埋地不能动,但 70% 即钻出 = 纯回血埋,可接受。

## 3. P2(中等缺口,改既有 micro)

### 3.1 MicroCyclones(新,`combat/terran/micro_cyclones.py`)
- **锁定 Lock-On `EFFECT_LOCKON`**:接敌时对最近**高价值/重甲/空中**敌单位锁定
  (`is_ready(LOCKON)` + 目标在射程 ~7 + power 高)。锁定后普攻跟走(GenericMicro)。

### 3.2 MicroSentries 放宽门(`micro_sentries.py`)
- 现 Force Field / Guardian Shield 要 `shield_percentage < 0.1` 才放 → 太晚。
- 改:**Guardian Shield** 在"接敌 + 敌方有远程单位(`engaged_power` 含 ranged)"即放(不等护盾快没)。
- Force Field 较难(落点复杂),本期**只放宽 Guardian Shield 时机**,FF 维持原样(避免乱放 FF 帮倒忙)。

### 3.3 MicroVipers 加 Abduct 威胁门(`micro_vipers.py`)
- 现 Abduct 无 `engaged_power` 限制 → 小规模乱拽。
- 加 `engaged_power.power > 10`(与 Parasitic Bomb/Blinding Cloud 对齐)+ 仍优先拽高价值
  (坦克/陆战之王/不朽/巨像等)。

### 3.4 MicroRavens 加自动炮台(`micro_ravens.py`)
- 现实现了干扰矩阵 + 反装甲导弹,缺 **Auto-Turret `BUILD_AUTOTURRET`**。
- 加:接敌(`engaged_power.power > ~5`)+ `unit.energy >= 50` + 冷却好 → 在敌群附近可建格落一座炮台。

### 3.5 MicroMedivacs 加加速(`micro_medivacs.py`)
- 治疗本身是引擎 autocast(正常,不动)。补 **Afterburners `EFFECT_MEDIVACIGNITEAFTERBURNERS`**:
  撤退(`move_type in {PanicRetreat, DefensiveRetreat}`)或赶赴远处战场时点加速,提生存/支援速度。

## 4. 验证(self-test,我自跑)
新增 `scripts/caster_ability_selftest.py`:debug 生 caster + 对应敌人,`VIBECRAFT_CASTER_TRACE=1`,
注入"进攻"让 caster 接敌,grep `CASTERTRACE <unit> <ability>` 断言每个技能至少触发 1 次。
case:
- ghost_snipe(鬼 vs 感染虫)/ ghost_emp(鬼 vs 叉子/哨兵堆)/ banshee_cloak(女妖 vs 枪兵)
- roach_burrow(残血蟑螂 vs 敌)/ cyclone_lockon / viper_abduct(威胁足)/ raven_autoturret /
  sentry_guardianshield / medivac_afterburner
非 realtime 可并行多开(参考 addon_selftest / proxy_chain_selftest)。
另:真局确认 **HT Feedback** 实际触发(审计说已修 energy≥50,眼见为实)。

## 5. 单测
- `test_sharpy_patch_audit.py`:micro_rules / 改过的 micro 方法进 PATCHED_METHODS(带 marker)。
- 新 micro 类的纯逻辑单测(mock unit/enemies/cd_manager → 断言返回正确 Action.ability):
  放 `tests/unit/test_caster_micro.py`。覆盖:能量不足不放、目标优先级、隐形/埋地门。

## 7. opus 评审结论处理(2026-06-18,实现前定稿,覆盖上文)

评审确认根因准、方向对。逐条落实:

- **M1 改名(必改)**:Raven 自动炮台 = `BUILDAUTOTURRET_AUTOTURRET`(原 `BUILD_AUTOTURRET` 错,
  枚举不存在→静默不放)。Cyclone 锁定本应 `LOCKON_LOCKON`(原 `EFFECT_LOCKON` 错)—— 但见 Y1 已砍。
  其余名(EFFECT_GHOSTSNIPE / EMP_EMP / BEHAVIOR_CLOAKON_GHOST|BANSHEE / EFFECT_MEDIVACIGNITEAFTERBURNERS /
  BURROW*_ROACH / GUARDIANSHIELD_GUARDIANSHIELD / EFFECT_ABDUCT)经核对 ✅ 正确。实现时所有 AbilityId
  用 vendor 现有引用交叉验证,别凭记忆。
- **M2 研究门(必改:删冗余)**:`cd_manager.is_ready(tag, ability)` **两参形式**已经覆盖
  tech 研究门 + 能量 + 冷却(底层 `get_available_abilities` 每帧算)。**不加** `ability in unit.abilities`
  / upgrade 查询(纯重复)。**红线:绝不给 is_ready 传第 3 个 `cooldown` 参数**(传了就退化成纯时间
  比较、绕过 available 查询 → 对没研究的技能空放)。显式 `unit.energy>=N` 多为冗余,但 **Cloak 保留**
  显式能量下限(要留攻击技余量)。
- **M3 Sentry(必改:改对地方)**:误读。`micro_sentries.py` 的 Guardian Shield(line 76-84)已是
  `range_power > 10` 即放(接敌就放,不等护盾掉);`shield_percentage < 0.1`(line 86)是**另一分支**
  管 FF/幻象。→ P2 §3.2 重写为:**只把 Guardian Shield 的 `range_power` 阈值调低(10→~6)** 让小规模
  也开盾;**不碰 shield_percentage 行**(碰了误伤 FF/幻象)。
- **M4 + Ghost 能量分段(必改)**:Ghost 用 `MicroStep`(不是 GenericMicro,否则普攻贴脸),
  **每条非 cast 路径都落 `stay_safe`**(别 return current_command,否则跟大军冲进 range 被秒)。
  能量分段:
  - `energy >= 75`:有合格 **EMP** 目标(敌群含护盾/高能 caster 且够密)→ EMP;否则有高价值生物
    目标 → Snipe。(**EMP 优先于 Snipe**,防 snipe 把能量拉到 75 下饿死 EMP)
  - `50 <= energy < 75`:只 Snipe。
  - `energy < 50`:Cloak(有威胁 + 未被探测 + 无 cast 目标 + 能量下限)或 stay_safe。
  - **Snipe 是引导技(~2s 定身)**:本帧已在 snipe orders 中 → 短路不发移动(别被 stay_safe 打断自己)。
- **C1 scope(必写明)**:micro 只跑在进了 combat group 的 `free_units`。**玩家用 `unit_claim` 单独
  编队偷袭的 banshee/ghost = Reserved,不进 group → 不走这套自动施法**。本方案**只覆盖"caster 跟
  大军一起参战"**。单独编队 caster 的自动施法是另一 scope,本期不做(明确告知用户)。
- **C2 Banshee**:用 `GenericMicro`(要普攻 + cloak)✅。记一笔:banshee 只打地面,敌全空时
  GenericMicro 仍会接近(边缘 case,不特判)。
- **R2 Raven autoturret**:落点**一次规划锁定**(CLAUDE.md 目标点铁规,别每帧重选)+ 优先级排在
  **干扰矩阵之后**(都费 50 能量,别抢)+ 造完别因能量见底乱撤。
- **Y1 砍 Cyclone lock-on**:实测 build 库无 Cyclone(`grep` strategies 空)→ 死代码,**本期不做**。
- **Y2 砍 Medivac afterburner 到 P3**:大军 combat 语境收益极低,**本期不做**。
- **Y3 Viper abduct 阈值**:`engaged_power.power > 10` 偏高门死小规模偷拽。改 **阈值 ~6-8**,
  且**目标 power 极高(坦克/巨像/BC/不朽)时豁免 engaged 门**(小股 viper 偷拽高价值有效)。

### 定稿后实现清单(P1 + 收敛后的 P2)
1. **新** `combat/terran/micro_ghosts.py`(MicroStep,EMP/Snipe/Cloak 能量分段 + 全路径 stay_safe + snipe 短路)
2. **新** `combat/terran/micro_banshees.py`(GenericMicro + cloak)
3. **改** `combat/zerg/micro_roaches.py`(`__init__(self)` 修签名)
4. **改** `combat/micro_rules.py`(注册 GHOST/BANSHEE/ROACH)
5. **改** `combat/protoss/micro_sentries.py`(Guardian Shield range_power 阈值 10→6)
6. **改** `combat/zerg/micro_vipers.py`(abduct 阈值 →6-8 + 高价值豁免)
7. **改** `combat/terran/micro_ravens.py`(加 BUILDAUTOTURRET_AUTOTURRET,落点锁定,排干扰矩阵后)
- 全部 `# vibecraft:` marker + `docs/sharpy-patches.md` 新节 + 改的 method 进 patch_audit。
- self-test:`scripts/caster_ability_selftest.py`(含反向 case:未研究隐形时 banshee 不放 cloak,实证 M2)。
- 单测 `tests/unit/test_caster_micro.py`(mock 验各技能门 + 目标优先 + 能量分段)。

## 6. 待评审重点(给 opus 评审 agent)
1. **Ghost 三技能能量互抢**:Snipe(50)/EMP(75)/Cloak(持续)同一能量池。优先级
   Snipe>EMP>Cloak 合理吗?cloak 会不会饿死 snipe?要不要按 energy 分段(≥75 优先 EMP/留 snipe,
   50-74 只 snipe,<50 才考虑 cloak 保命)?
2. **AbilityId 名对不对**(EFFECT_GHOSTSNIPE / EMP_EMP / BEHAVIOR_CLOAKON_BANSHEE/GHOST /
   EFFECT_LOCKON / BUILD_AUTOTURRET / EFFECT_MEDIVACIGNITEAFTERBURNERS / EFFECT_CORROSIVEBILE 等)——
   sc2 ids 里实际叫什么,评审核一遍别拼错(拼错=静默不放)。
3. **新 micro 会不会让脆皮 caster 冲太前**(鬼/女妖/Raven 该后排)——stay_safe/influence 兜底够不够?
4. **upgrade/研究门**:cloak/burrow/lock-on 没研究时 `cd_manager.is_ready` 会不会返 True 导致空放?
   要不要显式查 `AbilityId in unit(abilities)` 或 upgrade。
5. **P2 改 sentry/viper/raven/medivac** 会不会回归现有 override_acceptance / build_acceptance。
6. YAGNI:cyclone/medivac-afterburner 这种边际收益,值不值得本期做。
