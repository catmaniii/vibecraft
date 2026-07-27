以下是典型话语 → directives 示例（仅供学习模式，不要照搬 id 到不相关上下文）：

例 1：「切到双矿凤凰」
→ strategy_set: stage=midgame, strategy_id=iac_2base  (示意：若 catalog 里有 phoenix 版本则替换)

例 2：「下个 BG 出俩哨兵」
→ production_override: items=[{unit_type:Sentry, count:2}]

例 3：「先研闪烁」
→ tech_override: upgrade_id=Blink, priority=80

例 4a：「守家」/「所有部队回家防守」/「撤退」/「守一波」（一次性命令）
→ tactical_objective: verb=defend, target_area="natural", done_when=None, timeout_s=None
（A 类 verb，一次性命令，done_when 必须 None。PWA 点 × 解除）

例 4b：「全部撤回基地」/「回家」（一次性撤退）
→ tactical_objective: verb=retreat, target_area="main", done_when=None, timeout_s=None

例 4c：「接下来一直守家姿态」/「持续防守」/「保持防守状态」（持续姿态，明确说一直/持续）
→ tactical_objective: verb=defend, persistent=True, target_area=None, done_when=None
（persistent=True 表示持续姿态；bot 完成当次 attack 后仍保持 defend stance）

例 5：「凤凰举不朽」
→ unit_claim: selector={{unit_type:"Phoenix"}}, task={{primary_action:{{verb:"lift_target", target:{{kind:"unit_type", unit_type:"Immortal"}}}}}}, persistent=false
注:selector 没填 count → 所有 Phoenix 都 lift。

例 5c (safe_move 2026-05-24 用户):「棱镜贴边回基地」/「3 追猎安全回家」/「绕路回主基地」
→ move: selector={{unit_type:"WarpPrism", count:1}}, target={{kind:"named_spot", named_spot:"main"}}, safe=true, engage=false
注:safe=true 走 plan_drop_path 递归算法避开敌方主基地(参考 dt_drop 寻路)。
   玩家说"贴边/安全/绕路 回 X" → safe_move; 普通"棱镜回家" → move(safe=false)。
   完成判定:队伍**重心**距 target < 半径 → done(大区域如主矿半径宽,精确点窄)。

例 5d (engage 2026-06-06 用户):move 的 `engage` 控制沿途怎么走,与 `safe`(走哪条路)叠加。
   - engage=true → 沿途 attack-move(遇敌就打);engage=false → 普通 move(不主动接敌)。
   - **去对方/推进类**(到对方主矿/压上去/贴边到对方X)→ engage=true。
   - **回家/撤退/转移自家**(回基地/绕路回家/转去二矿)→ engage=false。
   「虚空贴边到对方主矿」→ move: selector={{unit_type:"VoidRay"}}, target={{kind:"named_spot", named_spot:"enemy_main"}}, safe=true, engage=true
   (贴边=safe 绕开主干路,engage=true 沿途遇敌照打 —— 两者叠加。)
   「所有虚空到对方主矿后面」→ move: ..., target={{kind:"named_spot", named_spot:"enemy_main_back"}}, safe=false, engage=true
   (精确点"后面"→ 到达半径窄;engage=true 推进。)

例 5b：「那个探机守气矿别动」/「一个农民去占瞭望塔」/「派 2 凤凰巡逻」
→ unit_claim: selector={{unit_type:"Probe", count:1}}, task={{primary_action:{{verb:"hold_position", target:{{kind:"named_spot", named_spot:"main_gas"}}}}}}, persistent=true
注:**selector.count 必填**当玩家说"一个/N 个"具体数量时。否则 selector 会把
**所有**同类型单位 Reserved(60 个农民全锁 → bot 不采气)。
- "一个农民..." → count=1
- "2 凤凰..." → count=2
- "派 3 追猎..." → count=3
- "所有/全部 Phoenix" / 玩家没说数 → count=null(不限,所有 Phoenix)
persistent=true 表示 standing order;玩家明确说"一直守"/"别动"/"持续"时使用。

例 5c (2026-05-25 新):「占瞭望塔」/「左边瞭望塔」/「右边的瞭望塔」/「使徒去右边瞭望塔」
→ unit_claim: selector={{unit_type:"Probe", count:1}}, task={{primary_action:{{verb:"hold_position", target:{{kind:"named_spot", named_spot:"watchtower_right"}}}}}}, persistent=true
注:**瞭望塔(Xel'Naga Tower)的 named_spot 只能是这三个**:
- `watchtower` — 任一(地图只 1 个或不分左右时用)
- `watchtower_left` — 按 x 坐标最左侧的
- `watchtower_right` — 按 x 坐标最右侧的
玩家说"左边"用 `watchtower_left`,"右边"用 `watchtower_right`。
**不要瞎猜** `right_tower` / `right_watchtower` / `main_ramp` / `natural_ramp` 等(都不存在,会解析失败单位站原地)。

例 6：「11 点盖水晶」/「斜坡下面建炮」/「二矿基地旁边盖个气」(2026-05-24 模糊地点)
→ build_at: structure_type="Pylon", named_spot="natural" (或 point=[x,y] 精确)
注:**优先用 named_spot** 而非 point — 玩家很少给精确坐标。
   完整 named_spot 列表(只能用这些,不要瞎猜):
   - main / natural / third (自方基地)
   - main_ramp / natural_ramp (自方斜坡顶)
   - enemy_main / enemy_natural / enemy_third (敌方基地)
   - enemy_main_ramp (敌方主斜坡)
   - main_gas / natural_gas / third_gas (自方气矿)
   - enemy_main_gas / enemy_natural_gas / enemy_third_gas (敌方气矿)
   - watchtower / watchtower_left / watchtower_right (Xel'Naga 瞭望塔)
   - forward (前线/前沿/最前线 — 自方已占领的最前沿矿点,典型用法
     "在前线造水晶折跃追猎"/"前线补个 BG"/"前线插水晶")
   "11 点盖水晶" → 用 named_spot 或 clock_X 别名(若 spot 表支持)。
   实在给不出来就 confidence < 0.5。
   **"前线" / "前沿" / "最前线" / "前面" 都用 named_spot="forward"**,
   **不要**误判成 enemy_main(那是敌方主基地,不是我方前线)。

例 6b：build_at vs structure_override 区别:
- build_at = 单次放一个建筑(必有具体地点 named_spot)
- structure_override = 补到 N 个目标数量(可选 location_hint,后端自动选位)

例 6c（2026-05-27 真实 crash 修正):「前线去个农民刷个水晶方便折跃追猎」/
「在前线插个水晶」/「前面补个 BG」/「前沿造个炮」
→ build_at: structure_type="Pylon", named_spot="forward"
注:**"前线"/"前沿"/"前面"/"最前线"都映射 named_spot="forward"**(我方推进点,
   多矿取距敌方最近的自方 nexus,单矿 fallback main_ramp 下)。
   **绝不**输出 named_spot="enemy_main" —— 那是敌方主基地,不是我方前线,
   送农民去敌方主基地造水晶等于送死。
   "前线 BG" → structure_type="Gateway", named_spot="forward"。

例 7：「那个叉子回来」
→ unit_release: selector={...}, return_to_role=IDLE

例 7b (Task #352 探路农民撤回):「让探路农民回来」/「把探路兵带回来」/「探路农民别探了」/「那个探路的农民回家」
→ unit_release: selector={unit_type:"Probe", count:1}, return_to_role=IDLE
注:**探路农民**（ScoutWorker 派出去巡逻对方基地的农民）撤回用 unit_release(Probe, count=1)。
   - selector.unit_type="Probe"（神族）/ "SCV"（人族）/ "Drone"（虫族）按种族决定
   - count=1 必填（只有 1 个探路农民；不填 → 所有农民被 release）
   - return_to_role=IDLE（让 sharpy 重新调度它回家采矿）
   - **不要**输出 strategy_cancel / tactical_objective / move —— unit_release 才能同时
     停止 ScoutWorker 的持续探路行为。

例 7c (2026-06-07 探路农民"改派"去做新任务 — 不是撤回采矿!):
「探路的农民回来吧，直接去占右边瞭望塔」/「让侦察兵去守瞭望塔」/「探路农民别探了，去占 X」
→ unit_claim:
     selector={primary_verb_prefix:"scout", count:1},
     task={primary_action:{verb:"hold_position",
           target:{kind:"named_spot", named_spot:"watchtower_right"}}},
     persistent:true
注:**"探路农民去做某新任务" = 把正在探路的那一个农民改派,不是撤回采矿。**
   - selector 用 **primary_verb_prefix="scout"** 选"正在探路的那个农民"(按任务身份,Director
     按指派时记的语意匹配回它 tag),**绝不**用泛泛 {unit_type:"Probe"}(会抓到家里采矿的另一个)。
   - **只发一条 unit_claim,绝不附带 unit_release** —— "回来吧 / 别探了"只是口语前缀,真实意图是
     "停止探路、去做新任务";unit_claim(改派)本身就停了探路。配 release 会把探路农民放回采矿、
     再随便抓个新农民去 → 玩家观感:探路农民跑回家、换个新农民占塔(真实踩坑)。
   - 对比例 7b:「探路农民回来」**后面没有新任务** → 才是纯 unit_release(撤回采矿);
     带"去 X / 去占 Y / 去守 Z"新任务 → 例 7c(改派,不 release)。

例 8：「切到双矿凤凰，然后凤凰好提对方农民」
→ [strategy_set, unit_claim(selector=phoenix, task=harass_workers)]

例 9：「取消当前剧本」/「停下」/「等等」/「先别按剧本走」/「取消所有剧本」/「停止刷兵」
→ strategy_cancel: stage=all
（玩家想清掉 bot 当前的宏观策略,bot 切到 sustain 模式：只 macro/守家,不主动出门。
  若玩家明确指定 stage：「取消开局剧本」→ stage=opening；「取消中期」→ stage=midgame）

--- done_when 典型 pattern ---

例 10（recon 火力侦查 — 中后期小股部队前压试探,**触发严格限定**）：
**只接受**: 明确提到「火力侦查」+ 区域,或「派 N 个 X 前压看看」(明确数量)。
**不要触发** recon 的语句:「试探/推上去/前压试试」 — 这些归 attack(mode=probe)
全军试探(见例 1g/2b)。
「火力侦查对方三矿」/「派 4 个追猎前压看看」/「火力侦查二矿」
→ [tactical_objective: verb="recon", target_area="enemy_natural",
   unit_count_hint=4, unit_type_hint=["Stalker"],
   done_when={kind:"any_of", conditions:[
     {kind:"enemy_killed_in_area", area:"enemy_natural", op:">=", value:3},
     {kind:"own_army_size_ratio", op:"<=", value:0.6},
     {kind:"time_elapsed_since", seconds:30, ref:"directive_issued"}
   ]},
   timeout_s: 90]
（recon 三条任意一条满足都退场：占到便宜（杀够人）/ 自己损耗超 40% / 30 秒到。
  recon 必带 done_when 且必填 unit_count_hint+unit_type_hint，区别 attack（committed
  大军，done_when=None）和 scout（纯视野，无伤亡阈值）。
  实施层面 recon 撤退系数 1.2 比 attack(probe) 1.0 更宽松,部队会先聚团再前压。）

例 10b(**试探性进攻 = attack mode=probe 全军,不是 recon**)：
「试探一下对方主基地」/「试探性进攻二矿」/「推上去看看」/「前压试试」
→ tactical_objective: verb="attack", attack_mode="probe", target_area="enemy_main",
  persistent=true, done_when=None
(全军 probe:部队先聚团,占便宜就占,占不到就撤;撤退系数 1.0 等敌方对等就撤;
 跟 all_in 区别 = all_in 强冲不撤,probe 见势不对就跑;跟 recon 区别 = probe 全军,
 recon 4 个小股 + 必带 done_when)

例 10c(**hold 全军坚守 — 聚团到点 + 站住不回家**,可带 target_area)：
「原地坚守」 / 「守住别动」 / 「钉在那别动」
→ tactical_objective: verb="hold", target_area=None, persistent=true, done_when=None
  (target=None → 当前 army_center 锁住聚团点,部队聚到那站住)

「部队到斜坡堵口」 / 「全部到主基地 ramp hold 一下」
→ tactical_objective: verb="hold", target_area="ramp", persistent=true, done_when=None

「部队到 3 矿基地站住别动」 / 「守二矿不准走」
→ tactical_objective: verb="hold", target_area="third", persistent=true, done_when=None
  (target_area 用 named_spot:ramp/natural/third/clock_11/watchtower 等)

跟 defend 区别:defend 回主基地家;hold 保持前线位置(聚到 target 站住)。
跟 retreat 区别:retreat 撤回家不出门;hold 不主动 attack 但占着位置。

例 11：「下个 BG 出 2 哨兵」
→ [production_override: items=[{unit_type:"Sentry", count:2}],
   done_when={kind:"unit_count_built_since", unit_type:"Sentry", op:">=", value:2},
   timeout_s: 60]
（自指令下达起，产出 2 个哨兵即完成）

例 11b（一句话多兵种 → **同一条 directive** 多 item + all_of done_when）：
「出 2 个叉子加 3 个追猎」
→ [production_override:
     items=[{unit_type:"Zealot", count:2}, {unit_type:"Stalker", count:3}],
     done_when={kind:"all_of", conditions:[
       {kind:"unit_count_built_since", unit_type:"Zealot",  op:">=", value:2},
       {kind:"unit_count_built_since", unit_type:"Stalker", op:">=", value:3}
     ]},
     timeout_s: 60]
（同次语音的多兵种任务整体跟踪、全部出齐才消失，作为一张 PWA 卡片。
  **绝不**拆成两条 directive。玩家下一次新的语音才开新卡片。）

例 11c（2026-06-09 真局踩坑 — "刷N兵到X" = 折跃门生产新兵到落点,**不是**移兵待命）：
「刷两个叉子到前线」/「在前线刷 2 叉子」/「折跃 3 追猎去二矿」
→ [production_override:
     items=[{unit_type:"Zealot", count:2}],
     warp_at={kind:"named_spot", named_spot:"forward"},
     done_when={kind:"unit_count_built_since", unit_type:"Zealot", op:">=", value:2},
     timeout_s: 60]
注:**"刷/折跃" = 折跃门出新兵**(production_override),地点用"到/去/在"都一样 → 填 `warp_at`。
   **绝不**译成 unit_claim(standby)/move/"到前线待命"(踩坑:真局把"刷两个叉子到前线"误译成
   "刷 2 个叉子到前线**待命**" → 发了 standby、兵没折跃)。"前线/到前线"→named_spot:"forward";
   "这里"→camera;"二矿"→natural。折跃门兵种(叉子/追猎/使徒/哨兵/电兵/DT)才带 warp_at,
   机械/空军不带。

例 12：「先研闪烁」
→ [tech_override: upgrade_id="Blink",
   done_when={kind:"tech_done", upgrade_id:"BlinkTech"},
   timeout_s: 90]
（闪烁研究完成即完成）

例 12b(2026-05-28 用户:多级升级看当前级别选下一级):
  context: upgrades_done=[PROTOSSGROUNDWEAPONSLEVEL1]
  「升级地面攻击」/「升攻」
→ [tech_override: upgrade_id="ProtossGroundWeaponsLevel2",
   done_when={kind:"tech_done", upgrade_id:"ProtossGroundWeaponsLevel2"},
   timeout_s: 200]
注:**绝不**输出 LEVEL1(玩家已升过,会立即 tech_done 完成,看似已下指令实际啥也没研)。
   规则:upgrades_done 含 LEVEL1 没 LEVEL2 → 输出 LEVEL2;含 1+2 没 3 → LEVEL3。
   3 级全升满 → confidence < 0.5 + interpretation_zh 说"已 +3,升满了"。
   玩家明确说"研 1 攻" / "升级 +2" 时按字面给 Level1/Level2。
   **upgrade_id / done_when.upgrade_id 必须用同一种 Camel 大小写**,不要既
   emit "ProtossGroundWeapons..." 又 emit "PROTOSSGROUNDWEAPONS...",**单条
   directive**(不是两条),否则后端两条都研、卡片重复显示。

例 12b-2(2026-06-08 用户:"攻防"=攻击+护甲两条 + "补BY 然后升级"组合,别空手返回):
  context: upgrades_done=[]
  「补一个 by 然后升级空军攻防」/「补个控制核心然后空军攻防一起升」
→ [structure_override: items=[{structure_type:"CyberneticsCore", delta:1}],
     done_when={kind:"structure_count", structure_type:"CyberneticsCore", op:">=", value:1}],
   tech_override: upgrade_id="ProtossAirWeaponsLevel1",
     done_when={kind:"tech_done", upgrade_id:"ProtossAirWeaponsLevel1"}, timeout_s:200],
   tech_override: upgrade_id="ProtossAirArmorsLevel1",
     done_when={kind:"tech_done", upgrade_id:"ProtossAirArmorsLevel1"}, timeout_s:200]
注:**"X 攻防" = 攻击 + 护甲两条 tech_override**(空军=Air,地面=Ground;级别按 upgrades_done 选下一级)。
   **"by" = BY = 控制核心 CyberneticsCore**(小写/语音转写也要认)。多动作一句话照样拆成多条,
   **绝对不要因为句子复杂就返回空 `{}`**(那会 ParseError,玩家看到"识别失败")——
   拆不全也要把能确定的几条 emit 出来。

例 12c(2026-05-28 用户:structure_override delta 增量 vs target_count 绝对):

【delta 增量】"补 / 造 / 再来 N 个 X" — 后端用当前 ready + delta 算 target,LLM 不看当前。
  「补一个 BF」
  → [structure_override:
       items=[{structure_type:"Forge", delta:1}],   ← **不**给 target_count
       done_when={kind:"structure_count", structure_type:"Forge", op:">=", value:1},
       timeout_s: 120]
  注:done_when 的 value 写 delta 字面值(1)就行,后端不严格用它对账,效果是
     "有进展"信号;真终止判定走后端 _exec_structure_override 的 ready vs target。

  「再来一个气矿」 → items=[{"structure_type":"Assimilator", "delta":1}]
  「补两个 VS」 → items=[{"structure_type":"Stargate", "delta":2}]

【target_count 绝对】"补到 / 造到 / 凑齐 N 个 X" — 绝对总数目标。
  「补到 8 个 BG」
  → [structure_override:
       items=[{structure_type:"Gateway", target_count:8}],   ← **不**给 delta
       done_when={kind:"structure_count", structure_type:"Gateway", op:">=", value:8},
       timeout_s: 180]

判定:**没**"补到/造到/补齐/凑齐/共要/总共"等绝对措辞 → 默认 delta(增量)。
   "补 N 个 X" 的 N=1/2/... 任何数都是 delta。
   "造一个 BF" / "再来一个 BF" / "多造一个 BF" 全部 delta=1。
   schema 强制 delta 与 target_count 二选一,都给会 validation error。

例 13a (看一眼 — SCOUT 单兵走一趟，到了即完成):
「看一眼对方主基地有哪些建筑」/「扫一下三矿」/「侦察一下二矿」/「探一下对方科技」
→ [tactical_objective: verb="scout", target_area="enemy_main",
   unit_count_hint:1, unit_type_hint:["Probe"],
   done_when={{kind:"vision_acquired", area:"enemy_main", hold_seconds:1}},
   timeout_s: 30]
注:scout = 一次性短暂查看;hold_seconds=1(瞬时);单位到达 vision range
   立即拿到信息 → done。看完单位由 sharpy 自动接管(默认回家采矿/守门)。

例 13b (持续视野 — VISION 派单位 hold N 秒):
「盯着对方主基地」/「保持二矿视野」/「持续看对方升科技」/「在对方主基地保持视野」
→ [tactical_objective: verb="vision", target_area="enemy_main",
   done_when={{kind:"vision_acquired", area:"enemy_main", hold_seconds:30}},
   timeout_s: 60]
注:vision = 持续保持;hold_seconds=30+(玩家说"一直/持续/盯着");到时间
   或玩家撤才 done。后端会派 1 单位 reserved 在 target 持续 hold。

例 14：「凤凰打死对方 5 个农民就回」
→ [tactical_objective: verb="harass", target_area="enemy_main",
   unit_type_hint:["Phoenix"],
   done_when={kind:"enemy_killed_in_area",
              area:"enemy_main", unit_type:"Probe", op:">=", value:5},
   timeout_s: 90]
（在主基地区域击杀 5 个探机即完成）

例 15：「30 秒后撤」
→ [tactical_objective: verb="retreat", persistent=True,
   done_when={kind:"time_elapsed_since", seconds:30, ref:"directive_issued"},
   timeout_s: 60]
（持续撤退姿态；自指令下达起经过 30 秒自动结束）

--- 边界 case ---

例 16 (复合 L1+L3): 「切凤凰运营,凤凰好骚扰对面农民」
→ [
    strategy_set(stage=midgame, strategy_id=phoenix_2base),  # L1
    unit_claim(selector={unit_type:"Phoenix"},
               task={primary_action:{verb:"harass",
                     target:{kind:"named_spot", named_spot:"enemy_main"}}},
               persistent=true,
               done_when={kind:"enemy_killed_in_area", area:"enemy_main",
                          unit_type:"Probe", op:">=", value:5},
               timeout_s:120)   # L3 standing + done
  ]

例 17 (L2 持续姿态 + done): 「守家直到闪烁好」
→ [tactical_objective(verb="defend", persistent=True, target_area=None,
   done_when={kind:"tech_done", upgrade_id:"BlinkTech"},
   timeout_s:300)]
（persistent=True 持续守家；done_when 用 tech_done 把 stance lifecycle 绑定到科技完成）

例 18 (撤销所有 standing): 「全部撤销 / 守家的都解散」
→ [strategy_cancel(stage="all")]
注:standing order 撤销由 PWA UI 处理 (revoke_directive 帧),不进 LLM directive

例 19 (无法解析 / 含糊): 「打吧」
→ confidence < 0.5,空 directives list,interpretation_zh 说明"指令含糊,
   建议:'打哪'/'打谁'/'什么时候'"
注:LLM 不猜测玩家本意,低置信走 ambiguous 路径

例 19b (待命指令 2026-05-24 用户): 「叉子到对方三矿待命」/「派 2 追猎在 natural 待命」/「3 个叉子三矿那守着」
→ [unit_claim(selector={{unit_type:"Zealot"}},
              task={{primary_action:{{verb:"standby",
                    target:{{kind:"named_spot", named_spot:"enemy_third"}}}}}},
              persistent=true)]
注:STANDBY 语义 = 移动到 target → 留守 + 受敌自动战斗 + 战斗后超出半径

例 19c (大舰骚扰 2026-06-19 用户): 「派一个大舰去骚扰对方二矿农民」/「派两个大舰骚扰主矿」/「一个大和去骚扰他三矿」
→ [unit_claim(selector={{unit_type:"BattleCruiser", count:1}},
              task={{primary_action:{{verb:"harass_workers",
                    target:{{kind:"named_spot", named_spot:"enemy_natural"}}}}}},
              persistent=true)]
注:
- 大舰/大和/大和舰/战巡 = BattleCruiser；verb 用 **harass_workers**（不是 L2 的 harass）。
- **必填 count**（玩家说"一个/两个"→ count:1/2）；没说数量 → 走 ambiguous 问"派几个大舰"。
- 矿区 named_spot：主矿=enemy_main / 二矿=enemy_natural / 三矿=enemy_third；
  **没指明矿区 → target 省略/None（bot 自动轮换找有农民的敌矿）**，不要追问矿区。
- persistent=true、**不加 done_when**（持续骚扰，玩家 ❌ 卡才停）。BC 会自动贴边绕到矿线打农民、
  残血传送回家修满再出。（前期 bot 已自动给每艘新大舰建这种卡，玩家可 ❌「自动骚扰」工厂卡停掉。）
   自动返回。后端每 tick 控位(_tick_standby_orders)。selector 可带或不带
   count(unit_count_hint),persistent=true(持续到玩家撤销)。
   "守 X 别动" 用 stance=hold(engagement_constraint);"在 X 待命" 用 standby。

例 20 (单位类型推断): 「3 个凤凰巡逻二矿」
→ [unit_claim(selector={unit_type:"Phoenix"},
               task={primary_action:{verb:"patrol",
                     target:{kind:"named_spot", named_spot:"natural"}}},
               persistent=true,
               unit_count_hint:3,
               timeout_s:99999)]
注:selector 不带 count (bot 自己挑数量),unit_count_hint 仅作提示

例 21 (vision 持续保持): 「在对方主基地保持视野」/「盯着对方主基地」
→ [tactical_objective(verb:"vision", target_area:"enemy_main",
                       done_when:{kind:"vision_acquired",
                                  area:"enemy_main", hold_seconds:5},
                       timeout_s:60)]
注:"保持视野" / "盯着" 是持续型 → tactical_objective(verb=vision);
   "看一眼" 短暂查看可走 tactical_objective(verb=scout)。
   都是 L2,**不是**顶层 scout(顶层 scout 一般给指定 unit 那种)。

例 22 (顶层 scout + 指定 unit + 方位): 「派探机侦察 11 点」/「派一个探机去 11 点看看」
→ [scout(selector:{unit_type:"Probe"},
         target:{kind:"named_spot", named_spot:"11_oclock"})]
注:玩家明确"派 X unit 去 Y 方位侦察" → 顶层 scout directive;
   selector + target 都给。如果玩家说"侦察一下 11 点"(没指定 unit),
   也可走顶层 scout(selector=None,bot 自选 idle probe);如果偏战术目标
   语义 "11 点那边查清楚" 可走 tactical_objective(verb=scout)。

--- structure_override + A/B done_when 规则例示 ---

例 23 (L4 补建筑 / structure_override): 「家里补到 8 BG」
→ [structure_override:
     items=[{structure_type:"Gateway", target_count:8, location_hint:"main"}],
   done_when={kind:"structure_count", structure_type:"Gateway", op:">=", value:8},
   timeout_s: 180]
注:structure_count 检查当前存量（含 pending），达到目标即 done。

例 24 (L4 多建筑 / **同一条 directive** 多 item + all_of done_when):
「ramp 放 2 cannon 1 BF」
→ [structure_override:
     items=[
       {structure_type:"PhotonCannon", target_count:2, location_hint:"ramp"},
       {structure_type:"Forge",        target_count:1, location_hint:"ramp"}
     ],
   done_when={kind:"all_of", conditions:[
     {kind:"structure_count", structure_type:"PhotonCannon", op:">=", value:2},
     {kind:"structure_count", structure_type:"Forge",        op:">=", value:1}
   ]},
   timeout_s: 180]
（同次语音的多建筑任务整体跟踪、全部造完才消失，作为一张 PWA 卡片。
  **绝不**拆成两条 directive。玩家下一次新的语音才开新卡片。）

例 24b (L4 气矿 / 2026-05-24 用户): 「二矿补 2 气矿」/「natural 补气」/「二矿放两个气」
→ [structure_override:
     items=[{structure_type:"Assimilator", target_count:2, location_hint:"natural"}],
   done_when={kind:"structure_count", structure_type:"Assimilator", op:">=", value:2},
   timeout_s: 90]
注:"二矿/分矿" → location_hint="natural"(自方); "三矿" → "third"。
   玩家说"补气矿"/"补气"/"放气矿"/"放气" 都映射到 structure_type="Assimilator"。
   不带 location_hint 默认家里(可省)。

例 24c (L4 人族气矿 + 数量歧义消解 / 2026-06-21 用户 #553): 人族「下二气」/「下两个气」/
「下两口气」/「补二气」/「下个气」/「补一个气矿」
→ 「下二气」/「下两个气」/「下两口气」/「补二气」(都=2 个):
   [structure_override:
     items=[{structure_type:"Refinery", delta:2}],
   done_when={kind:"structure_count_built_since", structure_type:"Refinery", op:">=", value:2},
   timeout_s: 90]
→ 「下个气」/「补一个气矿」/「补个气」(都=1 个):
   [structure_override:
     items=[{structure_type:"Refinery", delta:1}],
   done_when={kind:"structure_count_built_since", structure_type:"Refinery", op:">=", value:1},
   timeout_s: 90]
注（数量歧义，务必照此）:
   - **人族气矿 = "Refinery"**（不是 Assimilator，那是神族；虫族是 Extractor）。
   - **"二气" / "两气" / "两个气" / "两口气" = 数量 2**（一个基地两口气泉，背靠背各下一个）。
     这里的"二/两"是**基数 2**，**不是**序数"第二个"(那会错成 1)，也**不是**"二矿/natural"(那是位置)。
   - 数量 N 同时写进 `items[].delta=N` 和 `done_when.value=N`，两者必须一致。
   - "补"/"下"/"造"/"放" 在气矿语境同义(都是新建)，用 delta(新增)，done_when 用
     structure_count_built_since(数新建成的，不数已有的)。不带位置默认家里(可省)。

例 25 (A 类 done_when=None / 进攻): 「打对方二矿」/「打对方分矿」/「A 上对方三矿」
→ [tactical_objective: verb="attack", target_area="enemy_natural",
   done_when=None,
   timeout_s=None]
注:A 类 verb (attack / defend / retreat / vision) done_when 必须 None。
   task_monitor 设了 done_when 会立即判 done → bot 马上退回 sharpy 默认决策，
   跟玩家原意冲突。玩家通过 PWA 点 X 解除，不靠 done_when 自动结束。
   "全员别动"用 engagement_constraint(stance=hold)，不是 tactical_objective。
   **注意 target_area 取 named_spot 字面值**（enemy_natural / enemy_third / enemy_main）;
   玩家中文常说"分矿/二矿/三矿"，schema 用对应的 enemy_natural / enemy_third。
   ⚠️ 玩家不会说"自然"这种英文借词，他们说"分矿/二矿/三矿"。

例 26 (B 类 harass + done_when + unit_count_hint 必填): 「派 5 个凤凰去骚扰对方主基地」
→ [tactical_objective: verb="harass", target_area="enemy_main",
   unit_count_hint=5, unit_type_hint=["Phoenix"],
   done_when={kind:"enemy_killed_in_area", area:"enemy_main", unit_type:"Probe", op:">=", value:5},
   timeout_s: 90]
注:B 类 verb (harass / scout) done_when 必须给；unit_count_hint 必填。

例 27 (B 类无数量 → ambiguous): 「凤凰骚扰对面」
→ confidence < 0.5, 空 directives list,
   interpretation_zh="缺 unit_count_hint: 派几个凤凰去骚扰?"
注:B 类必须给数量，LLM 不要假设默认值，没有数量 → 走 ambiguous。

例 27b (2026-05-25 chrono boost / 星空加速 — 给建筑释放 nexus 技能):
「给两个 BF 星空加速」/「给锻炉加速」/「主基地给 BG chrono」/「给 VT 加速」
→ [unit_claim:
     selector={unit_type:"Nexus", count:2},  /* "两个 BF" → selector.count=2 = cast 2 次 */
     task={primary_action:{verb:"cast_ability",
           ability_id:"EffectChronoBoostEnergyCost",
           target:{kind:"unit_type", unit_type:"Forge"}}},
     persistent:false]
注:**星空加速 / chrono boost 不是 upgrade,是 Nexus active ability**!
   - **绝不**输出 tech_override(upgrade_id=ChronoBoost) — chrono 不是升级
   - **绝不**输出 production_override(Forge,2) — 玩家说"两个BF"是 cast 2 次,
     不是补建到 2 个
   - 正确语义:让 Nexus 对目标建筑(任意建筑)放 chrono boost
   - ability_id 必须 `EffectChronoBoostEnergyCost`(SC2 标准 enum 名)
   - target.kind="unit_type",target.unit_type 是 SC2 UnitTypeId 中建筑的 **精确名称**
     (大小写与下表一致;后端用 getattr(UnitTypeId, name.upper()) 查找):
     - "BF / 锻炉" → Forge
     - "BG / 兵营 / 折跃门" → Gateway
     - "BY / 控制核心" → CyberneticsCore
     - "VC / 议会 / 暮光议会" → TwilightCouncil
     - "VT / 圣堂档案 / 高塔" → TemplarArchive        ← 注意:无结尾 's'
     - "VR / 球 / 机械工厂" → RoboticsFacility
     - "VB / 球塔 / 巨像塔" → RoboticsBay
     - "VS / 星门" → Stargate
     - "VD / 黑暗神殿 / 隐刀塔" → DarkShrine
     - "VF / 舰队信标" → FleetBeacon
     - "BN / 主基地 / 折跃门 Nexus" → Nexus
     - "BB / 护盾电池" → ShieldBattery
     - "BC / 光子炮 / 炮台" → PhotonCannon
   - selector.count = 玩家说的次数("两个 BF" → 2;"给 BF 加速" → 1)
   - **任意建筑都可以被加速**,不只是上表已列举的 — 只要能对应到 UnitTypeId

例 27c (2026-05-29 chrono boost — VT/VC/VS/VB 等科技建筑加速示例):
「给 VT 加速」/「星空加速高塔」/「给圣堂档案 chrono」
→ [unit_claim:
     selector={unit_type:"Nexus", count:1},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EffectChronoBoostEnergyCost",
           target:{kind:"unit_type", unit_type:"TemplarArchive"}}},
     persistent:false]

「给 VS 星空加速」/「星门加速」
→ [unit_claim:
     selector={unit_type:"Nexus", count:1},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EffectChronoBoostEnergyCost",
           target:{kind:"unit_type", unit_type:"Stargate"}}},
     persistent:false]

「给 VB 加速」/「球塔 chrono」
→ [unit_claim:
     selector={unit_type:"Nexus", count:1},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EffectChronoBoostEnergyCost",
           target:{kind:"unit_type", unit_type:"RoboticsBay"}}},
     persistent:false]

「给 VR 和 VT 都加速一下」（复合 → 两条 directive）
→ [
    unit_claim(selector={Nexus,1}, task={cast_ability, TemplarArchive}),
    unit_claim(selector={Nexus,1}, task={cast_ability, RoboticsFacility}),
  ]

例 28 (2026-05-24 clarification 单位指代):
history 含 "派 1 个农民去占瞭望塔 → unit_claim(Probe, hold_position) id=d_xxx"。
当前玩家说: 「那个农民去对方三矿造水晶塔」
→ 不输出 directives,输出 clarification 字段让玩家选:
  interpretation_zh="请选择哪个农民" confidence=0.4 directives=[] clarification={
    question: "你要哪个农民去对方三矿造水晶塔?",
    options: [
      {label:"占瞭望塔那个", interpretation_zh:"调出占瞭望塔的 Probe 去造",
       directives:[
         {type:"unit_release", payload:{selector:{unit_type:"Probe", count:1}}},
         {type:"build_at", payload:{structure_type:"Pylon", named_spot:"enemy_third"}}
       ]},
      {label:"另派一个新农民", interpretation_zh:"新指派空闲 Probe 去造,不动瞭望塔那个",
       directives:[
         {type:"build_at", payload:{structure_type:"Pylon", named_spot:"enemy_third"}}
       ]}
    ]
  }
注:clarification 适合"指代不明 + 能列具体候选"。能列就用 clarification(对玩家友好,
   可直接点选);列不出来就走 ambiguous(让玩家重说)。option 数 2-4 个,label ≤ 20 字。

例 29 (clarification modifier 缺失):
history 含 "出 1 个哨兵 → production_override(Sentry,1)"。
当前玩家说: 「再来一些」(数量不定,但有 history 锚点上次是 Sentry)
→ clarification:
  question="再来几个 Sentry?" options=[
    {label:"再 2 个", directives:[production_override(Sentry,2)]},
    {label:"再 5 个", directives:[production_override(Sentry,5)]},
    {label:"造满人口", directives:[production_override(Sentry,10)]}
  ]
注:如果"再来 N 个" N 明确(如"再来 3 个"),直接 production_override 不要 clarification。
   只在数量真没说时才用。

--- 派单位到点 + hold position ---

例 30 (站瞭望塔 — unit_claim hold + move 2026-05-27):
「派一个追猎站左边瞭望塔」/「1 个追猎去占左边瞭望塔别动」
→ [
    unit_claim(selector={unit_type:"Stalker", count:1},
               task={primary_action:{verb:"hold_position",
                     target:{kind:"named_spot", named_spot:"watchtower_left"}}},
               persistent=true),
  ]
注:**瞭望塔站桩用 unit_claim(verb=hold_position, persistent=true)**，
   不要额外再发一条 move directive—— hold_position 本身包含"移到目标点再守住"语义。
   target.named_spot 只能是 watchtower / watchtower_left / watchtower_right 三者之一。
   count=1 必填（玩家说"一个"；不填 → selector 抢走所有追猎）。

例 31 (派单位到位后 hold — unit_claim + move 拆开写法 2026-05-27):
「派 2 追猎去守 5 点分矿」/「2 个追猎到 5 点分矿守着」
→ [
    unit_claim(selector={unit_type:"Stalker", count:2},
               task={primary_action:{verb:"guard_position",
                     target:{kind:"named_spot", named_spot:"own_clock_5"}}},
               persistent=true),
  ]
注:玩家说"守 N 点分矿" → 用 verb=guard_position(守某区域,受敌自动还击并归位),
   named_spot="own_clock_5"(自方锚点到 5 点方向的 expansion)。
   persistent=true = standing order，玩家通过 PWA 点 × 解除。
   **不要用 hold_position** — hold 是"原地钉死不移动"；guard_position 会移动到 target 守位。

例 31c (重选"正在守某地点的单位"去做新任务 — assigned_spot 2026-06-03):
「守瞭望塔的追猎去火力侦查对方基地」/「站塔那个追猎回来进攻」/「守 7 点那个叉子去推」
玩家指代"正在守某地点的 X"（X 之前已被派去守某 named_spot）→ 用 selector.assigned_spot
（它守的地点）+ unit_type 重选那个单位。Director 按**指派时记下的语意**匹配回它的 tag：
→ [unit_claim(selector={unit_type:"Stalker", assigned_spot:"watchtower", count:1},
              task={primary_action:{verb:"attack_move",
                    target:{kind:"named_spot", named_spot:"enemy_main"}}})]
注:火力侦查 = **attack_move**（边走边打，不是 recon —— recon 不是合法 unit verb）。
   **assigned_spot = 该单位被指派去守的 named_spot 标签**（不分左右就用 "watchtower"，
   会模糊命中 watchtower_left/right；明确"左边瞭望塔"用 "watchtower_left"；"7 点分矿"用
   "own_clock_7"）。配 unit_type 限类型、count 限数量。
   按任务类型重选（"守位的都回来"）可改用 primary_verb_prefix（"hold_"/"guard_"/"standby"）。
   **严禁发既无 unit_type/tag、也无 assigned_spot/primary_verb_prefix/group_id 的空
   selector** —— resolver 认不出 → 报"未找到匹配单位"，玩家以为没生效。

例 31d (按**物理位置**选"前线/最前面/后面那个" — selector.position 2026-06-08):
「前线那个追猎撤退吧」/「最前面的叉子退回来」/「前面那个不朽顶上去」/「后面那个追猎过来」
→ [unit_claim(selector={unit_type:"Stalker", position:"forward", count:1},
              task={primary_action:{verb:"retreat", target:{kind:"named_spot", named_spot:"main"}}},
              persistent=false)]
注:**"前线/前面/最前面的 X" = 按单位当前实际位置离敌最近的 → selector.position="forward"**;
   "后面/最后面/靠后的" → position="back"。配 unit_type + count(玩家说"那个"=1)。
   - **和 assigned_spot 区别**:assigned_spot 选"被你**指派去守**某地点的单位"(语意);position
     选"**当前物理位置**在最前/最后的单位"(bot 自然在前线打的追猎没被指派,只能用 position)。
   - 玩家报过 bug:"前线那个追猎撤退"被发成 assigned_spot="forward" → 选不到(没单位被指派去
     forward)。前线/前面这种**物理位置**词一律用 position,不要用 assigned_spot。

--- 钟点 / 方位表达 ---

例 32 (enemy clock spot — scout 2026-05-27):
「派一个农民去对方 11 点分矿侦察」/「探机去 11 点看看」
→ [scout(selector:{unit_type:"Probe", count:1},
         target:{kind:"named_spot", named_spot:"enemy_clock_11"})]
注:玩家说"N 点" → named_spot="enemy_clock_N"(对方锚点方向) 或 "own_clock_N"(自方)。
   **正确格式: `enemy_clock_11`，不是 `11_oclock` / `clock_11` / `enemy_11` 等**
   (KNOWN_SPOTS 白名单: `own_clock_1..12` / `enemy_clock_1..12` / `clock_1..12`)。
   "对方 / 敌方 / 他 X 点" → enemy_clock_X；"我方 / 自家 / 右边 X 点" → own_clock_X；
   没有"自方/敌方"前缀且地图锚点不明 → clock_X（以地图中心为锚点）。

例 33 (own clock spot + hold — 自方分矿 standby 2026-05-27):
「叉子在 7 点待命」/「2 个叉子去 7 点守」
→ [unit_claim(selector={unit_type:"Zealot", count:2},
              task={primary_action:{verb:"standby",
                    target:{kind:"named_spot", named_spot:"own_clock_7"}}},
              persistent=true)]
注:没有自方/敌方前缀时,结合上下文判断——玩家在本地行动说"7 点"通常指自方锚点，
   用 own_clock_7；如果玩家说"对方 7 点"则 enemy_clock_7。

--- 方位 alias ---

例 34 (direction alias — 骚扰 2026-05-27):
「派飞龙骚扰对方上面的分矿」/「龙去骚扰对面上边」
→ [tactical_objective(verb="harass", target_area="enemy_top",
   unit_type_hint=["Mutalisk"],
   done_when={kind:"enemy_killed_in_area", area:"enemy_top",
              op:">=", value:3},
   timeout_s:90)]
注:玩家说方位词 → 对应 named_spot 规则:
   上/北 → top(=clock 12) / 下/南 → bottom(=clock 6)
   左/西 → left(=clock 9) / 右/东 → right(=clock 3)
   左上 → top_left(=clock 11) / 右上 → top_right(=clock 1)
   左下 → bottom_left(=clock 8) / 右下 → bottom_right(=clock 5)
   前缀 enemy_* / own_* 锚点不同，完整列表:
   enemy_top / enemy_bottom / enemy_left / enemy_right
   enemy_top_left / enemy_top_right / enemy_bottom_left / enemy_bottom_right
   own_top / own_bottom / own_left / own_right
   own_top_left / own_top_right / own_bottom_left / own_bottom_right
   (以上均在 KNOWN_SPOTS 白名单中)

例 35 (方位 alias + unit_claim hold — 农民蹲点 2026-05-27):
「让农民蹲对方右下分矿」/「探机去对方右下角分矿盯着」
→ [unit_claim(selector={unit_type:"Probe", count:1},
              task={primary_action:{verb:"hold_position",
                    target:{kind:"named_spot", named_spot:"enemy_bottom_right"}}},
              persistent=true)]
注:方位 alias(enemy_bottom_right = clock 5 方向敌方扩张点)和 clock 表达
   (enemy_clock_5)语义等价,LLM 两者都能用;优先用方位别名当玩家说方位词,
   优先用 clock 当玩家明确说几点。
   **不要用 enemy_main_back 等不在白名单的名字** — 会解析失败单位站原地。

--- cast_ability 合球 / 技能释放 ---

例 36 (2026-05-30 合白球 / MORPH_ARCHON):
「所有电兵合成白球」/「电兵都合体」/「把 HT 都凑成 Archon」/「合白球」
→ [unit_claim:
     selector={unit_type:"HighTemplar"},  /* count=null → 所有电兵 */
     task={primary_action:{verb:"cast_ability",
           ability_id:"MORPH_ARCHON",
           target:{kind:"self"}}},
     persistent:false]
注:**ability_id 必须 `MORPH_ARCHON`**（不是 ArchonWarp / ARCHON_WARP / MorphArchon）。
   selector.unit_type="HighTemplar"（电兵 = High Templar）。
   target.kind="self"（合球不需要外部 target，2 个 HT 自动配对）。
   count=null → 后端把所有 HighTemplar 两两配对尽量多合；
   count=N → 合最多 N 个白球（需 2N 个 HighTemplar）。
   奇数 HT 时最后 1 个多出来，保持电兵状态，不强制合。
   **绝不**输出 production_override(Archon) —— 白球不能直接训练，只能两个电兵 morph。

「合 2 个白球」/「2 个电兵合体」
→ [unit_claim:
     selector={unit_type:"HighTemplar", count:2},
     task={primary_action:{verb:"cast_ability",
           ability_id:"MORPH_ARCHON",
           target:{kind:"self"}}},
     persistent:false]
注:「2 个电兵合体」= 合 1 个白球（2 HT → 1 Archon）；count=2 表示用 2 个 HT。
   「合 2 个白球」= 需要 4 个 HT；这时 count=2 是白球数目，后端乘 2 取 HT。
   **歧义时走 clarification** 问"2 个是用 2 个 HT 合 1 个，还是合 2 个白球用 4 个？"

例 37 (2026-05-30 放心灵风暴 / PSISTORM):
「电兵放心灵风暴」/「放 PsiStorm」/「HT 放风暴」
→ [unit_claim:
     selector={unit_type:"HighTemplar"},
     task={primary_action:{verb:"cast_ability",
           ability_id:"PSISTORM_PSISTORM",
           target:{kind:"named_spot", named_spot:"enemy_main"}}},
     persistent:false]
注:心灵风暴（PsiStorm）需要 target（落点）；若玩家没指定 target → 走 clarification
   或 confidence < 0.5。target.kind="named_spot" 给玩家口语区域，或
   target.kind="unit_type" 指定打什么类型单位聚集的位置（后端自动找）。
   **ability_id 必须 `PSISTORM_PSISTORM`**（不是 PsiStorm / PSIONIC_STORM）。

例 38 (2026-05-30 人族 — 枪兵嗑药冲 / EFFECT_STIM):
「枪兵嗑药冲」/「枪兵都兴奋剂」/「Marine stim 冲」
→ [unit_claim:
     selector={unit_type:"Marine"},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EFFECT_STIM",
           target:{kind:"self"}}},
     persistent:false]
注:**ability_id 必须 `EFFECT_STIM`**（枪兵）或 `EFFECT_STIM_MARAUDER`（船长）。
   兴奋剂不需要外部 target，target.kind="self"。
   selector.unit_type="Marine"（枪兵）/ "Marauder"（船长），按玩家指定的兵种填。
   **不要**混用两个 ability_id — 枪兵兴奋剂和船长兴奋剂是不同 enum。

例 39 (2026-05-30 虫族 — 飞蛇拉对面航母 / EFFECT_ABDUCT):
「飞蛇拉对面航母」/「Viper 拉那个大船」/「abduct 航母」
→ [unit_claim:
     selector={unit_type:"Viper"},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EFFECT_ABDUCT",
           target:{kind:"unit_type", unit_type:"Carrier"}}},
     persistent:false]
注:**ability_id 必须 `EFFECT_ABDUCT`**（不是 ABDUCT_ABDUCT）。
   target.kind="unit_type" 指定要拉的目标兵种；若玩家说"拉那个重甲/航母/战巡" →
   target.unit_type 分别为 Immortal / Carrier / BattleCruiser。
   飞蛇拉是点选 ability，后端挑距离最近的目标执行。
   没指定目标时走 clarification("要拉哪种单位?")。

例 39b (2026-06-20 人族 — 大舰传送回家 / EFFECT_TACTICALJUMP):
「所有大舰传送回基地」/「大和舰折跃回家」/「大舰都传送回去」/「战巡跳回基地」
→ [unit_claim:
     selector={unit_type:"BattleCruiser"},  /* count=null → 所有大舰；"一个大舰"→count:1 */
     task={primary_action:{verb:"cast_ability",
           ability_id:"EFFECT_TACTICALJUMP",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:false]
注:**ability_id 必须 `EFFECT_TACTICALJUMP`**（大舰/战巡/大和舰 = BattleCruiser 的传送/折跃技能，
   瞬移到目标点，**不是**走过去）。"传送/折跃/跳"回基地/回家 = 这条，**绝不**输出 move（move 会走回去）。
   target 是**落点**（cast_ability 的点选技能）：回家/回基地 → named_spot:"main"（己方主基地）；
   也可传送到别处 → 对应 named_spot / camera("这里")。

例 40 (2026-05-30 神族 — 叉子闪过去 / EFFECT_BLINK_STALKER):
「叉子闪过去」/「追猎闪到对方主基地」/「闪追 blink 进去」
→ [unit_claim:
     selector={unit_type:"Stalker"},
     task={primary_action:{verb:"cast_ability",
           ability_id:"EFFECT_BLINK_STALKER",
           target:{kind:"named_spot", named_spot:"enemy_main"}}},
     persistent:false]
注:**ability_id 必须 `EFFECT_BLINK_STALKER`**（不是 BLINK_STALKER / BLINK_BLINK）。
   闪烁需要 target 落点，target.kind="named_spot" 给区域 / "point" 给精确坐标。
   没指定目标时走 clarification("闪到哪里?")。
   selector.unit_type="Stalker"（追猎）；闪烁需要 Blink 升级完成，后端处理依赖检查。

--- 镜头跟随 / 产能封锁 ---

例 41 (2026-05-30 镜头跟随单个单位 / view_follow target_kind=unit):
「镜头跟着追猎」/「盯住那个凤凰」/「镜头跟一下叉子」/「跟随母舰」/「看那个不朽」
→ view_follow: target_kind="unit", unit_type="Stalker", unit_type_hint="追猎"   ← 追猎
   view_follow: target_kind="unit", unit_type="Phoenix", unit_type_hint="凤凰"   ← 凤凰
   view_follow: target_kind="unit", unit_type="Zealot", unit_type_hint="叉子"    ← 叉子
   view_follow: target_kind="unit", unit_type="Mothership", unit_type_hint="母舰" ← 母舰
   view_follow: target_kind="unit", unit_type="Immortal", unit_type_hint="不朽"  ← 不朽

注:**view_follow = 镜头跟随（Hook E ViewController）**，不给单位下行动命令。
   **绝不**用 unit_claim(verb=follow) 响应这些话——那是"让一个单位去跟另一个单位"。
   persistent=true（始终跟随，玩家 × 解除）；同时只允许 1 条 active，新来旧自动失效。
   target_kind="unit" 时 unit_type 用 canonical 名（Stalker/Phoenix/Zealot/Immortal/Colossus 等）。
   触发关键词:"镜头跟着 X 单位"/"盯住 X"/"跟随 X"/"看那个 X"/"镜头对着 X"/"让镜头跟着 X 走"。

例 42 (2026-05-30 停止造某种兵 / production_block):
「暂时不出追猎」/「停止造叉子」/「别造哨兵」/「不要出使徒」/「先暂停造凤凰」
→ production_block: unit_type="Stalker"   ← 追猎
   production_block: unit_type="Zealot"   ← 叉子
   production_block: unit_type="Sentry"   ← 哨兵
   production_block: unit_type="Adept"    ← 使徒
   production_block: unit_type="Phoenix"  ← 凤凰

注:**production_block = 持续抑制产量**，区别于 production_override（"必须出 N 个"增量）：
   production_block 是"暂停产线，直到玩家 × 才恢复"。
   **绝不**用 production_override(count=0) 代替——count=0 不合法且无语义。
   persistent=true（始终封锁，玩家 × 解除）。
   一条 directive 封锁一种兵（MVP）。
   触发关键词:"暂时不出 X"/"停止造 X"/"别造 X"/"不要出 X"/"暂停造 X"/"停产 X"。
   **解除**封锁 = 玩家点 PWA 卡片上的 × → revoke_directive → 恢复正常生产；
   **不要**让玩家再说"继续出追猎"才解除——那应该 revoke 旧 block，不是新 directive。

例 43 (2026-05-30 view_follow + production_block 复合):
「镜头跟着凤凰，顺便暂时不出追猎」
→ [
    view_follow(target_kind="unit", unit_type="Phoenix", unit_type_hint="凤凰"),
    production_block(unit_type="Stalker"),
  ]
注:复合句拆开，view_follow 管镜头，production_block 管产线，各自独立卡片。

例 44 (2026-05-30 镜头跟随大部队 / view_follow target_kind=army):
「镜头跟着大部队」/「跟主力」/「看主力部队」/「跟全军」/「镜头对着大部队」
→ view_follow: target_kind="army"

注:target_kind="army" 时不需要填 unit_type / unit_tag（bot 每 tick 算全军主力质心后 move_camera）。
   触发关键词:"跟大部队"/"跟主力"/"看主力部队"/"跟全军"/"镜头对着大部队"/"主力部队在哪跟着哪"。

例 45 (2026-05-30 镜头跟随侦查小队 / view_follow target_kind=squad):
「跟着火力侦查那波」/「看那波侦查」/「跟侦查小队」/「跟骚扰小队」/「镜头跟着那波骚扰」
→ view_follow: target_kind="squad"

注:target_kind="squad" 时不需要填 unit_type / unit_tag（bot 取第一个 active recon/harass squad 质心）。
   触发关键词:"跟着火力侦查"/"看那波侦查"/"跟侦查小队"/"跟骚扰小队"/"镜头跟着那波骚扰"。
   若当前没有 active squad，镜头不动（静默，玩家 × 解除）。

例 46 (2026-06-01 镜头跟随任务单位 / view_follow target_kind=task):
「镜头跟随探路农民」/「看那个探路的农民」/「跟侦察兵」/「跟巡逻的」/「跟守瞭望塔的」
→ view_follow: target_kind="task", task="scout", unit_type_hint="探路农民"   ← 探路/侦察
   view_follow: target_kind="task", task="patrol"                          ← 巡逻
   view_follow: target_kind="task", task="watchtower"                      ← 守瞭望塔
   view_follow: target_kind="task", task="harass"                          ← 骚扰

注:**"跟探路农民"是按任务身份跟，绝不是 target_kind="unit" unit_type="Probe"！**
   unit_type="Probe" 会跟到基地采矿的农民（错）；玩家要的是正在前线侦察的那一个。
   bot 按任务找单位（scout→正在 scout 的农民/单位 / patrol→巡逻单位 /
   watchtower→守塔单位 / harass→骚扰小队），单个→follow_unit 平滑跟，多个→质心。
   task 合法值:scout / patrol / watchtower / harass。
   触发关键词:"跟探路的"/"探路农民"/"侦察兵"/"巡逻的"/"守瞭望塔的"/"骚扰的农民"。
   若当前没有执行该任务的单位，镜头不动（静默，玩家 × 解除）。

--- 镜头"这里" camera / 语音编队 / 代理建造 / 巡逻两点 ---

例 47 (2026-06-01 镜头这里 — 派农民到镜头中心待命):
「派一个农民到这里待命」/「让农民过来这里待命」
→ [unit_claim:
     selector={unit_type:"Probe", count:1},
     task={primary_action:{verb:"standby",
           target:{kind:"camera"}}},
     persistent:true]
注:玩家说"这里/这边/此处" → target.kind="camera"（Director 运行时注入 camera_point 坐标）。
   **绝不自己填坐标** — LLM 不知道镜头坐标，camera 语义由 Director 动态注入。
   persistent=true = standing order，玩家 × 解除。

例 47b (2026-06-01 镜头这里 — 代理建造水晶到镜头):
「在这里修个水晶」/「农民来这边造个 BE」
→ [unit_claim:
     selector={unit_type:"Probe", count:1},
     task={primary_action:{verb:"build",
           target:{kind:"camera"}}},
     persistent:false]
注:build_at 无法表达 camera 目标（point/named_spot 都不接受 camera），改用
   unit_claim(verb=build, target=camera) — Director 会把农民派到 camera_point 附近造建筑。
   目前 unit_claim(verb=build) 不携带 structure_type，如果玩家说了具体建筑（如"水晶"），
   则**同时**发一条 build_at(structure_type="Pylon", named_spot=null)，两者联合执行；
   没说具体建筑则只发 unit_claim，bot 按上下文或默认行为决定建什么。
   persistent=false（一次性，农民建完回来采矿）。

例 47c (2026-06-01 镜头这里 — 试探进攻"这里"):
「大部队试探进攻这里」/「试探一下这边」
→ [tactical_objective:
     verb="attack", attack_mode="probe",
     target_area="camera",
     persistent=true, done_when=None]
注:tactical_objective.target_area 类型是 str|tuple|None；玩家说"这里"做进攻/防守
   目标时，填 target_area="camera"，Director 运行时注入镜头世界坐标（tuple）。
   **绝不自己填坐标** — LLM 不知道镜头坐标，camera 语义由 Director 动态注入。

例 47d (2026-06-07 集中/集合/聚集 — 把一批部队聚到某点独占停留):
「所有虚空到这里集中」/「虚空都到这里来」/「全部虚空过来这里集合」/「叉子聚到这里」
→ [unit_claim:
     selector={unit_type:"VoidRay"},
     task={primary_action:{verb:"standby",
           target:{kind:"camera"}}},
     persistent:true]
注:**"集中/集合/集结/聚集/都过来"= 把这批部队拿走、聚到某点独占停留待命**（玩家要"停那
   等我后续指令"，不是路过）→ **一律 unit_claim verb=standby persistent=true**。
   **绝不**用 move —— move 是一次性、到点就把单位交还给 bot 自动指挥(部队不会停那、还会被
   bot 拉去采矿/进攻)。selector 不填 count = 选全部该兵种("所有/全部虚空"→全选)。
   standby 会先把每个单位**移到镜头点**再持有,所以全部都会过来、到了就停住,持有到玩家 ×。
   地点是"这里"→ target kind=camera；具名点→ named_spot。

例 47e (2026-06-07 出兵集结点 — 设全局 rally,管"未来新出的兵"去哪):
「集结点设在这里」/「出兵都到这里集合」/「以后出的兵都去这里」/「新兵集结点放这」/「把集结点设到这」
→ [rally_point:
     target={kind:"camera"}]
注:**rally_point 和"集中/集合"(例 47d)是两码事,别混!**
   - **rally_point** = 设一个**全局集结点**,管**未来新出的兵**默认去哪(不动现有兵、不占控制权)。
     触发词:"集结点"/"出兵(都)去/到"/"新兵/新出的兵"/"以后出的兵"/"rally"。一直生效到玩家 ×。
     payload 只有 target(kind=camera/named_spot/坐标),**没有 selector**(不针对具体兵)。
   - **unit_claim standby**(例 47d) = 把**现有的一批兵**拿走、聚到某点独占停留(占控制权)。
     触发词:"〈某兵种〉到这里集中/集合/聚过来"(明确点了兵种 + 把现有的弄过去)。
   判别:句子在说"**新出的兵/出兵 去哪**" → rally_point;在说"**把(现有的)X兵 弄到哪**" → unit_claim。
   地点"这里"→ target kind=camera(Director 注入镜头坐标);具名点→ named_spot;**绝不自己填坐标**。

例 47f (2026-06-07 追加一个代理建造 — 复用正在外面建造的农民,别瞎编 chain_id):
「(那个农民)再到这里修一个 VS」/「你到这个位置再修一个星门」/「再帮我在这修个 BG」
→ [build_at:
     structure_type="Stargate", by_probe=true, named_spot="camera"]
   （activate_when 留空 = null,立即生效）
注:这是**追加一张**单独的代理建造卡,接在之前正在进行的"修水晶+VS"代理建造后面。
   - **绝不**用 `chain_structure_ready` + 自造 chain_id(如 "d_131f")去续之前那条链 ——
     你**不知道**之前命令的真实 chain_id,瞎编的链不存在 → 卡永不激活(真实踩坑)。
   - `by_probe=true` + activate_when=null → Director 用 `by_probe` **自动复用"当前持有的那个代理
     建造农民"**(就近选),水晶早建好了不用再等链。
   - **绝不**发 structure_override(那是"家里建",不是派农民去镜头点建)。
   - 地点"这里/这个位置" → named_spot="camera"(Director 注入镜头坐标)。

例 47g (2026-06-09 在镜头处开矿/下主基地 — 看着矿区下基地):
「在这开矿」/「在这里开个矿」/「在这下主基地」/「在这下个矿」/「这片矿开了」/「在这造个基地」
→ [build_at:
     structure_type="Nexus", by_probe=true, named_spot="camera"]
注:玩家**看着一片矿区**说"在这/这里 + 开矿/开个矿/下主基地/下个矿/造基地/这片矿开了"
   → **一律 build_at Nexus(by_probe) 到镜头点**(派农民去那建主基地开矿)。
   - 地点"这里/这片"在镜头里 → named_spot="camera"(Director 注入镜头坐标)。
   - **绝不**用 structure_override(那是家里建)、**绝不**自己填坐标。
   - **落点不用你操心**:Director 会自动判断——离最近矿很近就贴矿摆正,离得有点远会**弹确认**
     让玩家选"修正到矿区/就在原地",太远(故意挡路)就原地建。你只管发 build_at Nexus + camera。
   - **对比**:没指地点的"再开个矿/开矿/扩一个"(玩家没框矿区、只想多开一个)→ 用
     **expansion_override**(bot 自己选下一个分矿点),不是 build_at。区别:有没有"这里/这片"指当前镜头。

例 47h (2026-06-19 镜头框选 — selector.near_camera 选"镜头内的一批单位/建筑"):
「把镜头内的追猎编成 2 队」/「屏幕上的叉子都编成 1 队」
→ [group_assign:
     group_id=2,
     selector={unit_type:"Stalker", near_camera:true}]
「镜头里的兵全部进攻这里」/「视野内的部队压上去这边」
→ [unit_claim:
     selector={role:"ARMY", near_camera:true},
     task={primary_action:{verb:"attack_move", target:{kind:"camera"}}},
     persistent:false]
注:**"镜头内的/屏幕上的/这屏的/视野里的/看到的这些 〈X〉" = selector.near_camera=true**
   （Director 在下达那刻把镜头视口框内的匹配单位/建筑固化成具体 tags，不随镜头移动变化）。
   - **必须**同时带 `unit_type`（具体兵种/建筑，如 Stalker/Bunker）**或** `role`（ARMY=所有军队，
     不含农民；ANY/IDLE 含农民）。**裸 near_camera 会被拒**。
   - **区别于位置的"这里/这边"**：那是 `target.kind="camera"`（一个**落点**）；near_camera 是
     **选哪些单位/建筑**（框一批）。两者可同句组合（"镜头里的兵进攻这里"= near_camera 选兵 + camera 落点）。

例 47i (2026-06-19 建筑回收 — salvage 把地堡/感应塔拆掉拿回矿):
「把地堡卖了」/「回收那个碉堡」/「拆掉地堡」/「地堡拆了」
→ [salvage:
     selector={unit_type:"Bunker"}]
「镜头内的地堡都回收了」/「把屏幕上的碉堡全卖了」
→ [salvage:
     selector={unit_type:"Bunker", near_camera:true}]
注:**"回收/拆/拆掉/拆了/拆除/卖/卖掉 + 建筑" = salvage directive**（一次性，done_when 通常 null）。
   - selector 选哪些建筑：`unit_type`（Bunker=碉堡/地堡，SensorTower=感应塔）/ `near_camera` / `tags`。
   - 后端按建筑类型自动选回收 ability；不可回收的建筑（补给站等）会被友好拒绝，不报错。
   - **只对己方建筑**。**绝不**用 structure_override（那是"建/补到 N 个"，不是拆）。

例 47j (2026-06-19 地堡货舱控制 — 进兵/放兵):
「往地堡塞 4 个枪兵」/「让枪兵进地堡」/「把兵塞进碉堡」/「进兵」
→ [bunker_cargo:
     action="load",
     selector={unit_type:"Bunker"},
     count:4]
「把地堡的兵放出来」/「卸载地堡」/「地堡放兵」/「碉堡里的兵出来」
→ [bunker_cargo:
     action="unload",
     selector={unit_type:"Bunker"}]
注:**"进兵/装兵/往地堡塞兵" = bunker_cargo(action=load)**；**"放兵/卸载地堡/兵出来" = bunker_cargo(action=unload)**。
   - action 只有 "load" / "unload" 两种，不要用其他值。
   - count 仅 load 时有意义（默认 4=满载）；unload 不需要 count。
   - selector 选地堡（unit_type:"Bunker" / near_camera / tags）；非地堡建筑被静默跳过。
   - **绝不**把进兵/放兵映射成 tactical_objective 或 unit_claim。

例 48 (2026-06-01 语音编队 — 把运输机编成1队):
「把运输机编成 1 队」/「运输机 1 队」
→ [group_assign:
     group_id=1,
     selector={unit_type:"WarpPrism"}]
注:group_assign payload = {group_id, selector}。selector 按玩家说法填 unit_type。
   group_id 必须在允许范围内（编队上限见 rules 的"语音编队"段，默认 1-5）。done_when=null（编队是持久结构）。
   **绝不**自造越界 group_id（0 或上限+1）—— 越界照实填、由系统报错，不要 clamp 成合法值。

例 48b (2026-06-01 语音编队 — 把2个农民编成3队):
「把 2 个农民编成 3 队」/「2 个探机 3 号队」
→ [group_assign:
     group_id=3,
     selector={unit_type:"Probe", count:2}]
注:count=2 表示只选 2 个 Probe 加入 3 队（非全部农民）。

例 48c (2026-06-01 语音编队 — 解散/取消/清除队伍):
「释放 2 队」/「取消 2 队」/「清除 2 号队」
→ [group_clear:
     group_id=2]
注:三种说法（释放/取消/清除）全部映射 group_clear；group_id 按玩家说的数字填。

例 48d (2026-06-01 语音编队 — 编队指挥):
「1 队到这里待命」
→ [unit_claim:
     selector={group_id:1},
     task={primary_action:{verb:"standby",
           target:{kind:"camera"}}},
     persistent:true]

「2 队去对方三矿待命」
→ [unit_claim:
     selector={group_id:2},
     task={primary_action:{verb:"standby",
           target:{kind:"named_spot", named_spot:"enemy_third"}}},
     persistent:true]
注:selector 只填 group_id，Director 运行时解析为该队的 tags。
   target 可以是 camera（"这里"）或 named_spot，正常填写。

例 48e (2026-06-04 编队指挥 — 让某队进攻):
「1 队进攻对方三矿」/「1 队打对方三矿」/「让一队 A 过去对面三矿」
→ [unit_claim:
     selector={group_id:1},
     task={primary_action:{verb:"attack_move",
           target:{kind:"named_spot", named_spot:"enemy_third"}}},
     persistent:true]
注:**进攻/打/A过去 = verb attack_move**(facade 下 unit.attack,会边走边打)。
   **绝不**用 tactical_objective —— 它是全军指令、无 group_id，会让全军动而这队不动。
   只要玩家点名"N 队",就走 unit_claim + selector{group_id:N}。

例 48f (2026-06-04 编队指挥 — 火力侦查 = 也是 attack_move):
「2 队火力侦查这里」/「2 队顶一波这里」/「二队试探进攻这里」
→ [unit_claim:
     selector={group_id:2},
     task={primary_action:{verb:"attack_move",
           target:{kind:"camera"}}},
     persistent:true]
注:对**具体某队**说火力侦查 = attack_move(不是全军 tactical recon)。
   "这里" → target kind=camera。

例 48g (2026-06-04 编队指挥 — 撤退/回防):
「3 队撤退」/「3 队回防」/「让三队拉回来」
→ [unit_claim:
     selector={group_id:3},
     task={primary_action:{verb:"standby",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:true]
注:撤退/回防/拉回来 = standby 到己方主基地 named_spot:"main"。
   "让某队恢复自由(交回 bot)"是另一回事 → group_clear(例 48c),别混。

例 49 (2026-06-01 出 vs 出到 — 在二矿修8个BG):
「在二矿修 8 个 BG」
→ [structure_override:
     items=[{structure_type:"Gateway", delta:8, location_hint:"natural"}],
     done_when={kind:"structure_count", structure_type:"Gateway", op:">=", value:8},
     timeout_s:300]
注:"修 8 个"没有"到"字 → **delta=8**（新增 8 个，不管当前已有几个）。
   "在二矿" → location_hint="natural"。

「补到 14 个 BG」
→ [structure_override:
     items=[{structure_type:"Gateway", target_count:14}],
     done_when={kind:"structure_count", structure_type:"Gateway", op:">=", value:14},
     timeout_s:300]
注:"补到 14 个" 有"到"字 → **target_count=14**（绝对总数目标）。
   schema 强制 delta 与 target_count 二选一，同时给会 validation error。

例 50 (2026-06-01 代理建造 β — 派农民去前线造水晶 — 两卡组合):
「派农民去前线修个水晶」
→ [
    unit_claim(selector={unit_type:"Probe", count:1},
               task={primary_action:{verb:"standby",
                     target:{kind:"named_spot", named_spot:"forward"}}},
               persistent:true),
    build_at(structure_type:"Pylon",
             named_spot:"forward",
             by_probe:true,
             activate_when:{kind:"unit_arrived", area:"forward", within_grid:5.0}),
  ]
注:β 两卡方案。卡1 unit_claim(persistent=true) 派农民去"forward"并留在那。
   卡2 build_at(by_probe=true) 等 activate_when=unit_arrived 满足（农民到点）后激活，
   bot 自动找最近农民下 build。两卡靠 unit_arrived 串联，零新 directive 类型。

例 50b (2026-06-06 代理建造连锁 — 修水晶,水晶好了在能量场修 BG):
「派农民去对方 6 点分矿修个水晶,水晶好了在旁边修个 BG」
→ [
    unit_claim(selector={unit_type:"Probe", count:1, chain_id:"proxy_6oclock"},
               task={primary_action:{verb:"standby",
                     target:{kind:"named_spot", named_spot:"enemy_clock_6"}}},
               persistent:true),
    build_at(structure_type:"Pylon", named_spot:"enemy_clock_6", by_probe:true,
             chain_id:"proxy_6oclock",
             activate_when:{kind:"unit_arrived", area:"enemy_clock_6"}),
    build_at(structure_type:"Gateway", named_spot:"enemy_clock_6", by_probe:true,
             chain_id:"proxy_6oclock",
             activate_when:{kind:"chain_structure_ready", chain_id:"proxy_6oclock"}),
  ]
注:**神族机制** —— Gateway(及绝大多数建筑)必须建在 Pylon 能量场内。所以"修水晶再修
   BG"= 先 Pylon、后 Gateway,且 **Gateway 必须等那个 Pylon 真建好**。
   - 卡1 用 `unit_claim` verb=`standby`、**persistent=true** 带 **chain_id**:standby 会先把
     农民**移到工地**再稳稳持有它(脱离 bot,不被拉走),整条链都它干。**造建筑必须用
     这种持有方式**——别用一次性 move(到点会释放,农民会被 bot 抢去采矿/探路)。
     建完农民继续待命,直到玩家 ×。
   - 卡2 修 Pylon:**payload 层带 chain_id**（同链），activate_when=unit_arrived(农民到点就修)。
     **by_probe=true 的 build_at 卡必须在 payload 带 chain_id**，Director 据此保证用链上同一农民。
   - 卡3 修 Gateway:**payload 层带 chain_id** + **activate_when=chain_structure_ready(同 chain_id)** ——
     精确等"卡2 那个农民造出来的那一个 Pylon"建好(后端抓住该建筑 tag 判定,不看全局 Pylon 数)。
   **通用模式(连续指令)**:"前一步造出的那个建筑建好了,再做下一步"→ 后一步
   activate_when 用 `chain_structure_ready`(同 chain_id);**不要**用全局 `structure_count`
   (家里已有同类建筑就会被当场放行)。

例 50c (2026-06-06 代理建造连锁 — 修水晶,然后修 N 个建筑 — 必须 N 张 by_probe):
「派一个农民去这里修个水晶,然后修两个 VS」
→ [
    unit_claim(selector={unit_type:"Probe", count:1, chain_id:"proxy_here"},
               task={primary_action:{verb:"standby", target:{kind:"camera"}}},
               persistent:true),
    build_at(structure_type:"Pylon", by_probe:true, chain_id:"proxy_here",
             named_spot:"camera",
             activate_when:{kind:"unit_arrived", area:"camera"}),
    build_at(structure_type:"Stargate", by_probe:true, chain_id:"proxy_here",
             named_spot:"camera",
             activate_when:{kind:"chain_structure_ready", chain_id:"proxy_here"}),
    build_at(structure_type:"Stargate", by_probe:true, chain_id:"proxy_here",
             named_spot:"camera",
             activate_when:{kind:"chain_structure_ready", chain_id:"proxy_here"}),
  ]
注:**代理建造链里"然后修 N 个 X"= N 张 by_probe build_at,绝不发 structure_override！**
   - "修两个 VS" → **两张** `build_at`(structure_type:"Stargate", by_probe:true),不是一张
     structure_override(delta:2)。每张 build_at 只建一个,N 个就 N 张。
   - **这些 VS 必须 by_probe + 同地点 + chain_structure_ready**,跟着农民在代理点建。
     **绝对不要**降级成 `structure_override`(delta/target_count)——那是"家里建",
     bot 会在主基地出 VS、抢光钱,代理点反而没钱、玩家的 VS 落空(这是真实踩过的 bug)。
   - **camera("这里")目标的 build_at**:用 `named_spot:"camera"`(point 留空),Director 注入
     镜头实际坐标。**绝不**写 `point:{kind:"camera"}` 之类——point 只接受坐标 tuple/null,
     写 dict 会校验失败、整条命令解析失败。named_spot 是地名时(natural/enemy_clock_9)直接填地名。
   - 判据:只要句子是"派/让农民去〈某点〉修…,然后/接着修…",**整条链所有建筑都 by_probe
     build_at**(Pylon 先、其余 chain_structure_ready 等 Pylon),哪怕玩家没说"水晶好了/在旁边"。
   - "然后修两个 VS" 即使没显式说"在那/水晶好了",也默认**接着在同一代理点**建(承前一步地点)。

例 51 (2026-06-01 巡逻两点 — 农民在敌方11点和三矿之间巡逻):
「农民在对方 11 点分矿和三矿之间巡逻」
→ [unit_claim:
     selector={unit_type:"Probe", count:1},
     task={primary_action:{verb:"patrol",
           target:{kind:"named_spot",
                  named_spot:"enemy_clock_11",
                  waypoints:[
                    {kind:"named_spot", named_spot:"enemy_clock_11"},
                    {kind:"named_spot", named_spot:"enemy_third"}
                  ]}}},
     persistent:true]
注:waypoints 是一个 TargetSpec 数组，每项都是合法 TargetSpec。
   patrol 时 target 本身作为第一个锚点，waypoints 补充完整路线（[A, B]）。
   persistent=true（持续巡逻到玩家 × 解除）。

「3 个凤凰在二矿和对方主基地之间巡逻」
→ [unit_claim:
     selector={unit_type:"Phoenix", count:3},
     task={primary_action:{verb:"patrol",
           target:{kind:"named_spot",
                  named_spot:"natural",
                  waypoints:[
                    {kind:"named_spot", named_spot:"natural"},
                    {kind:"named_spot", named_spot:"enemy_main"}
                  ]}}},
     persistent:true]

例 52 (2026-06-01 巡逻两点 — 第一点是"这里"):
「追猎在这里和对方三矿之间巡逻」
→ [unit_claim:
     selector={unit_type:"Stalker"},
     task={primary_action:{verb:"patrol",
           target:{kind:"camera",
                  waypoints:[
                    {kind:"camera"},
                    {kind:"named_spot", named_spot:"enemy_third"}
                  ]}}},
     persistent:true]
注:第一个巡逻点是"这里"（camera），target.kind="camera"，waypoints[0] 也是 camera。
   Director 运行时注入 camera_point，两者引用同一坐标（不重复计算，幂等）。

例 53 (2026-06-02 连续指令 — 一个农民连续走多步，靠 activate_when 串联):
「农民先去右瞭望塔，再去对方 11 点分矿，然后在对方二矿修个水晶，最后回家采矿」
→ [
    move(selector={unit_type:"Probe", count:1, chain_id:"probe_scout_build"},
         target={kind:"named_spot", named_spot:"watchtower_right"},
         done_when:{kind:"unit_arrived", area:"watchtower_right", within_grid:5.0}),
    move(selector={chain_id:"probe_scout_build"},
         target={kind:"named_spot", named_spot:"enemy_clock_11"},
         activate_when:{kind:"unit_arrived", area:"watchtower_right", within_grid:5.0},
         done_when:{kind:"unit_arrived", area:"enemy_clock_11", within_grid:5.0}),
    build_at(structure_type:"Pylon", named_spot:"enemy_natural", by_probe:true,
             activate_when:{kind:"unit_arrived", area:"enemy_clock_11", within_grid:5.0}),
    unit_release(selector={chain_id:"probe_scout_build"}, return_to_role:"IDLE",
                 activate_when:{kind:"chain_structure_ready", chain_id:"probe_scout_build"}),
  ]
注:第 1/2 条是"路过"→ 用 `move`、**persistent=false**（到点即走,不留守）。4 条卡链,
   第 1 条无 activate_when（立即起），done_when=到右瞭望塔。第 2-4 条每条
   activate_when = 上一条的 done_when 同款条件，未满足时灰色"未激活"卡挂着，到点才激活。
   **同一农民接力靠 chain_id**："probe_scout_build" 在每条 selector 都带；第 1 条带具体
   unit_type+count（Director 解析后绑定 chain_id→该农民 tag），第 2/4 条 selector 只带
   chain_id → 解析回同一农民。build_at(by_probe) 自动找最近农民（=出门那个链上农民）。

--- 状态属性指代（WP-B）---

例 54 (2026-06-04 残血追猎撤回来):
「残血的追猎撤回来」/「受伤的追猎拉回基地」/「血少的追猎回家」
→ [unit_claim:
     selector={unit_type:"Stalker", health_below_pct:50},
     task={primary_action:{verb:"retreat",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:false]
注:health_below_pct=50 → 只选血量 < 50% 的追猎，其余不动。
   "残血/受伤/血少" 阈值常用 50；"快死" 用 20；"轻伤" 用 70。
   与 unit_type AND 关系：先筛类型再筛血量。

例 55 (2026-06-04 受伤不朽拉回基地):
「受伤的不朽拉回基地」/「不朽血量低的撤」/「残血不朽撤」
→ [unit_claim:
     selector={unit_type:"Immortal", health_below_pct:60},
     task={primary_action:{verb:"retreat",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:false]
注:不朽（Immortal）血量阈值 60 稍高，因为不朽本身血厚、受伤 60% 已需救援。
   阈值没有唯一答案，按玩家语气判断：含糊的"受伤"用 50-60，明确"快死"用 20-30。

例 56 (2026-06-04 盾破虚空撤):
「盾破的虚空撤」/「护盾没了的辉光舰回来」/「盾爆了的虚空拉回基地」
→ [unit_claim:
     selector={unit_type:"VoidRay", shield_below_pct:20},
     task={primary_action:{verb:"retreat",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:false]
注:shield_below_pct=20 → 只选护盾 < 20% 的虚空（神族盾破）。
   "盾破/护盾没了/盾爆了" → shield_below_pct；"残血/受伤" → health_below_pct。
   两者**不要混淆**：血量和护盾是独立维度（神族单位两个都有）。

例 56b (2026-06-04 血量低 AND 盾破):
「又残血又盾破的追猎撤」/「血量低护盾也没了的追猎回来」
→ [unit_claim:
     selector={unit_type:"Stalker", health_below_pct:50, shield_below_pct:20},
     task={primary_action:{verb:"retreat",
           target:{kind:"named_spot", named_spot:"main"}}},
     persistent:false]
注:两个字段同时填 → AND 关系（血量 < 50% 且护盾 < 20% 才选中）。
   单独只说"残血" → 只填 health_below_pct；只说"盾破" → 只填 shield_below_pct。

--- 偷矿（stealth_mine）---

例 57 (2026-06-10 偷矿 — 在镜头处偷矿):
「在这偷矿」/「在这里偷一个矿」/「去偷矿」/「开隐蔽基地」/「对方三矿偷个矿」
→ stealth_mine: point=[0, 0], worker_target=16, with_gas=true, on_attack="flee"
注:point=[0,0] = 占位，Director 注入 camera_point（玩家需先将镜头移到目标矿区）。
   **绝不自己填坐标**；cell_id 不填（默认 0，Manager 分配）。
   无 done_when / timeout（持久指令，PWA × 撤销）。
   "去对方三矿偷个矿"同样发 stealth_mine(point=[0,0]) —— 玩家说前镜头应指向对方三矿。
   区别于"开三矿"（expansion_override，bot 常规扩张）；stealth_mine 是**隐蔽**采矿点。

例 58 (2026-06-10 偷矿 + 农民数调整):
「在这偷矿，多派点农民」/「偷个矿，给 20 个农民」/「这里偷矿，少点就 8 个农民够了」
→ stealth_mine: point=[0, 0], worker_target=20, with_gas=true, on_attack="flee"  ← 多农民
→ stealth_mine: point=[0, 0], worker_target=8,  with_gas=true, on_attack="flee"  ← 少农民
注:"多派点农民 / 多点工人" → worker_target 调高（默认 16，最多 24）。
   "少派 / 只要 N 个" → 对应调低。玩家没提农民数 → 用默认 16，**不要**凭感觉猜。
   "不要偷气 / 只偷矿" → with_gas=false；默认 true（有气矿同时偷）。

例 60 (2026-06-13 持续征兵 — 以后新出的虚空都编到一队):
「以后新出的虚空都编入 1 队」/「后面出来的虚空自动加 1 队」/「将来造的虚空都是一队的」
→ [group_assign:
     group_id=1,
     selector={unit_type:"VoidRay"},
     auto_enroll=true]
注:auto_enroll=true 使 directive 持续运行——每次有新虚空出现就自动加入 1 队。
   只有玩家说"以后/后面/将来/持续/每次出来都/自动加"这类**时间延伸**词时才加 auto_enroll:true；
   普通"把虚空编成 1 队"→ 不加（默认 false，立即执行一次 SET 入队后 done）。
   玩家 × 取消时停止持续征兵，已入队的单位保留在 1 队。

例 61 (2026-06-13 持续征兵 — 后面新出的追猎都去二矿待命):
「后面新出的追猎都到二矿待命」/「将来造的追猎统一去分矿待命」
→ [unit_claim:
     selector={unit_type:"Stalker"},
     task={primary_action:{verb:"standby",
           target:{kind:"named_spot", named_spot:"natural"}}},
     persistent=true,
     recruit_new=true]
注:recruit_new=true 配合 persistent=true 使 directive 持续运行——每次有新追猎出现就发"到二矿待命"。
   selector 不填 count（不限数量，新出多少算多少）。
   仅"后面/以后/将来新出的"话语触发 recruit_new=true；普通"把追猎都派去二矿"→ 普通 unit_claim（无 recruit_new）。

例 59 (2026-06-10 多片偷矿 — 两条独立 stealth_mine):
「偷两个矿点」/「在这偷两处」/「偷这里和对方三矿各一个」
→ [
    stealth_mine(point=[0, 0], worker_target=16, with_gas=true, on_attack="flee"),
    stealth_mine(point=[0, 0], worker_target=16, with_gas=true, on_attack="flee"),
  ]
注:一句话里"两处 / 两个点"→ emit 两条 stealth_mine，Manager 各自分配不同 cell_id。
   两条 point 都是 [0,0]（同一 camera_point，运行时同位置）。
   **偷不同区域**需分两次说话、各自移镜头：
     第1次:镜头指向矿区A → 说"在这偷矿" → emit 一条 stealth_mine。
     第2次:镜头移到矿区B → 说"在这也偷矿" → emit 另一条 stealth_mine。
   **不要**把多个点塞进一条 directive。

例 60 (2026-06-18 人族产能建筑挂件决策 P1 — addon_decided):

例 60a「补4bb,2科技2双倍」(玩家明确给 mix → addon_decided=True + 挂件 item)
→ structure_override:
    items=[
      {structure_type:"Barracks", delta:4, addon_decided:true},
      {structure_type:"BarracksTechLab", delta:2},
      {structure_type:"BarracksReactor", delta:2},
    ]
注:玩家明确指定挂件分配 → Barracks item 的 addon_decided=True (不弹窗)；
   同时 emit BarracksTechLab(delta=2) + BarracksReactor(delta=2)；
   挂件 item 自身不需要 addon_decided 字段。

例 60b「补5bb,3科技其它不挂」(部分挂 TechLab,其余不挂)
→ structure_override:
    items=[
      {structure_type:"Barracks", delta:5, addon_decided:true},
      {structure_type:"BarracksTechLab", delta:3},
    ]
注:玩家说"其它不挂"→ 不 emit BarracksReactor；addon_decided=True 表示已决定；
   5 - 3 = 2 个兵营不挂任何附件（不需要显式 item）。

例 60c「补3bb不挂附件」(明确不挂 → addon_decided=True,无挂件 item)
→ structure_override:
    items=[
      {structure_type:"Barracks", delta:3, addon_decided:true},
    ]
注:玩家说"不挂附件/不挂/不要挂件" → addon_decided=True，不 emit 任何挂件 item。
   Director 不弹窗，直接执行。

例 60d「补4bb」(没提挂件 → addon_decided=False,触发 Director 弹窗)
→ structure_override:
    items=[
      {structure_type:"Barracks", delta:4, addon_decided:false},
    ]
注:玩家只说"补 N 个兵营"没提挂件 → addon_decided=False（默认值）。
   Director 收到后弹 3 选项确认弹窗：a)不挂 b)推荐 N科技+M双倍 c)取消。
   **重工/机场同理**（Factory/Starport，挂件换对应前缀 FactoryTechLab/StarportReactor 等）。

例 61a「派 3 个农民修理大舰」(repair 指令：修理 ≠ build)
→ repair:
    selector={unit_type:"Battlecruiser"},
    worker_count:3
注:「大舰」是单位，不是建筑，绝不能是 build_at/structure_override。
   "修理/维修/修一下" → repair 指令；持续型，所有目标满血后自动完成。

例 61b「修一下那个地堡」(repair 一个建筑)
→ repair:
    selector={unit_type:"Bunker"}
注:player 没指定农民数 → worker_count 省略（后端默认 3）。

例 61c「家里的残血大舰都修一下」(repair 全部目标)
→ repair:
    selector={unit_type:"Battlecruiser"}
注:"残血/全部/都" → 不限数量，selector 不填 count（后端选所有匹配单位）。
   不要误输出 build_at(Battlecruiser) —— Battlecruiser 是单位，后端会拒绝 build。

例 62 (#580 大舰群骚扰 — 所有大舰去骚扰，target=auto):
「所有大舰去骚扰」/「大舰去骚扰吧」/「大舰全去骚扰矿区」
→ [unit_claim:
     selector={unit_type:"BattleCruiser"},
     task={primary_action:{verb:"group_harass", target:null}},
     persistent=true,
     recruit_new=true,
     target_count:null]
注:target=null → auto picker 选最优矿（Director/GroupHarassAct 决策）。
   target_count=null = 无上限，所有大舰进骚扰群。
   recruit_new=true：新造大舰自动并入群（持续征兵）。

例 63 (#580 大舰群骚扰 — 指定艘数 + 指定矿):
「派3个大舰去骚扰二矿」/「三艘大舰骚扰二矿」/「骚扰对方二矿，出3艘大舰」
→ [unit_claim:
     selector={unit_type:"BattleCruiser"},
     task={primary_action:{verb:"group_harass", target:{kind:"named_spot", named_spot:"enemy_natural"}}},
     persistent=true,
     recruit_new=true,
     target_count:3]
注:target 锁定敌方二矿；target_count=3 最多 3 艘入群。
   玩家说矿区对应：主矿→enemy_main，二矿→enemy_natural，三矿→enemy_third。
   无指定矿区（"骚扰吧/随便"）→ target:null（auto picker）。

例 64 (#580 大舰群骚扰 — 减到 N 艘 / 留 N 艘):
「大舰骚扰减到2艘」/「骚扰的大舰留2个」/「大舰骚扰只留2艘」
→ [unit_claim:
     selector={unit_type:"BattleCruiser"},
     task={primary_action:{verb:"group_harass", target:null}},
     persistent=true,
     recruit_new=true,
     target_count:2]
注:「减到N/留N」= 给绝对 target_count=N；Director 检测已有 group_harass claim → 更新它（幂等，不新建）。
   不给相对减量——直接给目标绝对值。
   target=null：若玩家未再指定矿区，延续现有目标（Director 保留旧 target）。

例 65 (#580 大舰群骚扰 — 停止骚扰 / 都别烧了):
「停止大舰骚扰」/「大舰别骚扰了」/「大舰都别烧了」/「骚扰取消」
→ [unit_claim:
     selector={unit_type:"BattleCruiser"},
     task={primary_action:{verb:"group_harass", target:null}},
     persistent=true,
     recruit_new=true,
     target_count:0]
注:target_count=0 = 暂停：释放所有群内 BC 归还 bot + 停止征兵；directive 留存（✗ 才真删）。
   之后说"继续骚扰/大舰去骚扰" → 新 unit_claim 或 target_count 调高，Director 幂等恢复。

--- 野矿侦查（快捷语句）---

例 66 (2026-06-29 野矿轻侦查 — 2 条 scout 分头扫对方二矿+三矿):
「侦查野矿」/「看对方开矿没」/「查一下对方分矿」/「看看对方扩张没」/「查查对方扩没扩」
→ [
    scout(selector:null,
          target:{kind:"named_spot", named_spot:"enemy_natural"},
          done_when:{kind:"vision_acquired", area:"enemy_natural", hold_seconds:1},
          timeout_s:30),
    scout(selector:null,
          target:{kind:"named_spot", named_spot:"enemy_third"},
          done_when:{kind:"vision_acquired", area:"enemy_third", hold_seconds:1},
          timeout_s:30),
  ]
注:**两条 scout** 同时发出，分头扫两个扩张候选点（enemy_natural=二矿、enemy_third=三矿）。
   selector=null → bot 自选空闲最便宜的工人/侦察单位（各派一个，互不干扰）。
   done_when=vision_acquired(hold_seconds=1) → 看到即算，立刻回来，不停留。
   **绝不只发一条 scout** —— 只覆盖一个点，二矿三矿都查才算"查野矿"。
   **绝不用 recon** —— 轻侦查只需要便宜单位快速看一眼，不要动用战斗小队。

例 67 (2026-06-29 火力侦查野矿 — recon 小队去对方扩张点，顶得住截击):
「火力侦查野矿」/「带兵查野矿」/「带队侦查对方分矿」/「出点兵查查对方有没有开矿」
→ [tactical_objective:
     verb="recon",
     target_area="enemy_natural",
     unit_count_hint:4,
     unit_type_hint:["Stalker"],
     done_when:{kind:"any_of", conditions:[
       {kind:"vision_acquired", area:"enemy_natural", hold_seconds:2},
       {kind:"own_army_size_ratio", op:"<=", value:0.6},
       {kind:"time_elapsed_since", seconds:30, ref:"directive_issued"}
     ]},
     timeout_s:90]
注:玩家喊"火力侦查野矿/分矿"→ target_area="enemy_natural"（最重要的扩张候选点）。
   unit_type_hint 按种族：神族=["Stalker"] / 人族=["Marine"] / 虫族=["Zergling"]。
   没说具体兵种时用本族便宜战斗兵，unit_count_hint=4（默认小股）。
   区别于轻侦查（例 66）：火力侦查带战斗单位，顶得住拦截 —— 玩家自己决定要不要升级。
   **绝不拆成两条 recon**（战斗单位别分兵；想查三矿让玩家另发指令）。
   done_when 三条任一满足即撤退：拿到视野 / 损耗超 40% / 30 秒到。

--- 人族建筑起飞/移动 + 农民基地调度（2026-07-08）---

例 68a「主基地飞起来」(structure_move 原地起飞悬停):
→ structure_move:
    from_spot="main",
    to_spot=null
注:玩家只说"飞起来/起飞"没给目的地 → to_spot 不填（null），后端起飞后原地悬停。
   structure_type 也不用填 —— 后端按 from_spot 附近实际的 townhall（可能已升 OrbitalCommand）
   自动解析真实起飞 ability，LLM 不需要判断当前是 CC 还是 OC。

例 68b「主基地飞到二矿」(structure_move 起飞→飞→降落):
→ structure_move:
    from_spot="main",
    to_spot="natural"
注:"飞到/飞去 + 地点" → to_spot 填对应 named_spot（二矿=natural）。
   一次性持续型动作（后端状态机推进起飞→飞行→降落，落地后自动完成），done_when 不填。
   **只有人族能起飞**；星球要塞(PlanetaryFortress)不能起飞，后端会友好拒绝，LLM 照样按
   structure_move 处理，不用自己判断建筑当前类型。

例 68c「降落在这里」(structure_move 对已在飞的基地下新指令，落到镜头点):
→ structure_move:
    from_spot="main",
    to_spot="camera"
注:"降落在/落在/落地"是玩家对**已经起飞悬停**的基地说的。LLM **不用判断该建筑当前是不是
   在飞**——from_spot 照样填这座基地原本的位置 named_spot（如"主基地"→"main"），后端 FIND
   会自动同时找落地的和已经在飞的那座。"这里/这" → to_spot 用 camera（Director 注入镜头点）。
   降落点会自动 snap 到该点附近**最优贴矿 townhall 采矿位**（不是随便找个能放下的空地）。

例 68d「基地飞到三矿」(对已在飞的基地再给一个新目的地):
→ structure_move:
    from_spot="main",
    to_spot="third"
注:玩家可能是"先说飞起来、过一会又说飞去三矿"两句话分开下——这条跟例 68b 结构完全一样，
   LLM 不用特殊处理"已经在飞"这件事，正常按 structure_move(from_spot, to_spot) emit 即可。

例 69a「主矿的农民优先采水晶」(worker_task 持续优先采矿):
→ worker_task:
    from_base="main",
    action="prioritize_minerals"
注:"优先采水晶/优先挖矿/别采气了" → action="prioritize_minerals"，持续生效直到玩家再改
   （当前是全局采矿优先开关，单基地阶段跟"主矿"等价）。**绝不**用 structure_override 或
   production_override —— 这不是造建筑/出兵，是调整现有农民的采集分配。

例 69b「主矿的农民去二矿采矿」(worker_task 一次性转移):
→ worker_task:
    from_base="main",
    action="transfer_to_base",
    to_base="natural"
注:"去/调去/搬去 + 目标基地 + 采矿" → action="transfer_to_base"，to_base 必填。
   "全部"隐含默认：from_base 所有正在采矿的农民（不含已经在采气/在建的）都会被调去 to_base。
   一次性动作，后端持续钉住数秒防止被自动分配拉回，settle 后交还 bot 采矿池，done_when 不填。
