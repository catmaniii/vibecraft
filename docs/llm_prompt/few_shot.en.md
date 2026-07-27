English-input examples (appended only when the player's UI language is English). These
supplement the main examples above — the directive schema and named_spot/verb/selector rules
are identical; only the player's phrasing is English. Always emit the same structured
directives (enum unit/building/upgrade ids stay canonical English: Stalker, Immortal, Gateway,
VoidRay, Pylon, …). Write `interpretation_zh` in English.

Ex E1: "switch to two-base phoenix" / "go skytoss"
→ strategy_set: stage=midgame, strategy_id=<pick the closest id from the catalog>
(Only use a strategy_id that exists in the catalog above; otherwise confidence < 0.5.)

Ex E2: "build two more gateways" / "add 2 gates"
→ production_override: items=[{building:Gateway, count:2}]

Ex E3: "make four stalkers" / "train 4 stalkers out of the gate"
→ production_override: items=[{unit_type:Stalker, count:4}]

Ex E4: "research blink first" / "get blink"
→ tech_override: upgrade_id=Blink, priority=80

Ex E5: "defend the natural" / "everyone back home and hold" / "pull back" (one-shot)
→ tactical_objective: verb=defend, target_area="natural", done_when=None, timeout_s=None
"retreat to main" / "fall back home" → tactical_objective: verb=retreat, target_area="main", done_when=None
"keep a defensive stance from now on" (persistent posture) → tactical_objective: verb=defend, persistent=True, target_area=None

Ex E6: "attack their main with everything" / "push their third"
→ tactical_objective: verb=attack, target_area="enemy_main" (or "enemy_third"), done_when=None
(Pushing toward the enemy → this is the all-army objective; it does NOT touch claimed units.)

Ex E7: "send one probe to scout the natural" / "two phoenix patrol their third"
→ unit_claim: selector={{unit_type:"Probe", count:1}}, task={{primary_action:{{verb:"scout", target:{{kind:"named_spot", named_spot:"natural"}}}}}}, persistent=false
(selector.count is REQUIRED when the player says "one/two/N". "all phoenix" / no number → count=null.)

Ex E8: "send a probe here to build a pylon, then two stargates" (camera-relative proxy build)
→ card 1 unit_claim (persistent) claims 1 Probe and moves it to the camera point;
   card 2/3 build_at(by_probe=true, structure_type="Pylon" then "Stargate", activate_when chains).
(Use the camera point when the player says "here"/"this spot" and gave no coordinates.)

Ex E9: "void rays go to the back of their main" / "stalkers edge into their natural"
→ move: selector={{unit_type:"VoidRay"}}, target={{kind:"named_spot", named_spot:"enemy_main_back"}}, safe=false, engage=true
("edge in / sneak around" → safe=true; pushing toward the enemy → engage=true; going home/retreat → engage=false.)

Ex E10: "group the void rays into group one" / "make these stalkers group 2"
→ group_assign: group_id=1, selector={{unit_type:"VoidRay"}}
(Voice group ids are 1..5. "release group 1" → group_clear: group_id=1.)

Ex E11: "build a pylon at the ramp" / "cannon at the bottom of the ramp"
→ build_at: structure_type="Pylon", named_spot="main_ramp"
(Prefer named_spot over raw coordinates. Only use named_spots from the canonical list above —
never invent ones like "right_ramp"/"choke".)

Reminder: building names use hotkey letters in display but the directive's enum id is the
canonical English building name (Gateway/Stargate/Pylon/…). Unit ids are official English
(Stalker/Immortal/VoidRay/…). Never refuse an English command you can map; if truly unsure,
emit a clarification or confidence < 0.5 rather than guessing a non-existent id.
