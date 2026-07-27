# VibeCraft Player Guide

> Command StarCraft 2 with your voice — Commander Mode for veterans who can no longer keep up with the mechanics

---

## I. What Is It

Your game sense is still sharp, but your hands can't keep up anymore. VibeCraft lets you **issue strategic and micro commands from your phone** (typed text, or your phone's built-in speech-to-text), while the AI executes all the mouse and keyboard actions for you.

One sentence: **you are the commander, the AI is your executive officer**.
- You say on your phone: "4BG pressure, if that fails go double expansion"
- The AI follows that build on the PC: producing units, expanding, sending armies, managing everything
- **The live game is streamed straight to your phone** — you watch the action on your phone, make strategic calls, issue commands at key moments
- The PC just runs SC2 and the AI; you **only ever look at your phone**, never the PC, and never touch the keyboard or mouse

### Who This Is For

- Veteran SC2 players who still have the game sense but not the mechanics
- Want to play a few games with old friends without having to out-APM anyone
- Want to focus purely on the "strategy layer" without worrying about probe production, control groups, or multi-tasking

### Who This Is NOT For

- Traditional ladder players who want to compete on APM
- Complete newcomers who don't know the basic unit types

---

## II. Overall Design

### Division of Labor

| | You (Player) | AI (Base bot) |
|---|---|---|
| **Economy** (probes, supply, gas, expansions) | — | 100% handled |
| **Production / Build** | Decide the big picture | Executes strictly by build |
| **Basic combat** (focus fire, stutter, retreat) | — | Basic level automatic |
| **Strategic decisions** (attack timing / tech switch / expansion timing) | **Core value here** | Defaults to conservative |
| **High-difficulty micro** (FF / Storm / Blink in / Graviton Beam) | **Must issue manually** | Does not use proactively |

**Bot wins ~50% vs Hard AI with no player input**. Active participation wins Hard, deep engagement wins Harder.

### The 4 Command Layers (most important)

VibeCraft internally maps every sentence you speak to one of 4 layers. Read this table and you'll understand how long commands last and how they expire:

| Layer | Name | Examples | Duration | How it ends |
|---|---|---|---|---|
| **L1** | Macro strategy | "4BG", "switch to IAC", "go Skytoss" | Full phase (opening/mid/late) | Switch to a new build, or click x on the strategy card |
| **L2** | Tactical command (no specific units) | "attack 2nd base", "Phoenix harass", "full retreat" | One-shot / A-type (attack/defend/retreat) persists until cancelled | Card x button |
| **L3** | Unit standing / one-shot task | "DT guard gas don't move", "send probe to 11 o'clock" | One-shot → returns to bot when done; *persistent* tag → held until cancelled | Card x button |
| **L4** | Production override | "make 4 Zealots", "add 8 BG", "research Blink", "expand here" | Until goal is met | **Auto-disappears on completion** / click x to cancel early |

**Priority pyramid rule** (key):
- Nothing from you at any layer → bot decides everything (follows build)
- You issue a command at some layer → **the part "locked" by your command** the bot can't touch, but **everything else still runs autonomously**
  - Example: you say "DT guard gas" → bot won't reassign that DT to fight, but it continues commanding all other units
  - Example: you say "add 8 BG" → bot won't build more Gateways itself, but continues following the build for tech and units
- You cancel a command → the bot's autonomy "floats back up" at that layer

### Pacing

- **Strategic commands rate-limited to 10 seconds** — forces you to think before speaking
- **Camera movement unlimited** — drag the minimap whenever
- **1.5-second parse delay** — gives you a cancel window

### Transparency

On your phone you can see:
- Which build the bot is currently running, and which phase it's in (L1)
- All currently active L2/L3/L4 commands, each with progress conditions
- What the bot is currently thinking ("attacking / defending / expanding", with a short reason)
- Your last 10 commands and the AI's interpretation

You'll never face "I thought it was doing 4BG but it went macro" confusion.

---

## III. Setup and Operation

<!-- chat-skip-start -->
### 0. Choose Your Race

VibeCraft supports all three races. Specify with `--my-race` at startup:

```bash
# Protoss (default)
uv run vibecraft serve --my-race Protoss

# Zerg
uv run vibecraft serve --my-race Zerg

# Terran
uv run vibecraft serve --my-race Terran
```

If `--my-race` is not specified, Protoss is the default. You can only pick one race per game; you cannot switch mid-game.

### 1. Preparation

1. **Start the VibeCraft bot service on your laptop** (command line: `uv run vibecraft serve --my-race <race>`, wait for the QR code)
2. **Scan the QR code with your phone** — opens the mobile control console in your browser
3. **Go into SC2 and create a custom game**:
   - Slot 1: VibeCraft Bot (same race you chose at startup) ← your AI
   - Slot 2: SC2 built-in AI (any race + difficulty)
   - You **join Slot 1 as a player** (just don't touch the keyboard or mouse)
4. **Start the game**

### 1.5 Multiplayer (1v1 with a friend — supported since 2026-06-12)

One PC runs two SC2 instances in a LAN lobby; you and your friend each command your own bot from your phones:

1. **Entry page**: Open the server address on your phone and you'll see the entry page — enter a **username**, select/add a **server** (address + room token; scanning the QR code auto-fills the current server), then tap [Connect].
   - **[Share QR Code] button** (on the entry page footer and lobby header): tap to show a QR code for **the current URL** — the exact address and room token your phone is using. A friend scans it with another phone and goes straight to the same game page, no manual URL typing needed. The popup also has a one-tap copy button.
   - **Server list shows names, not full URLs**: when you open via QR code or a URL with a room token, the PWA asks the server for its name (`GET /api/server-info`) and shows that **friendly name** (e.g. `close_test`) with a dimmed `host:port` line below — **no full URL, no room token shown**.
   - **Name your server**: The host puts `name: close_test` in `config/servers/<name>.yaml` (can also include `token`/`port`/`ip`) and starts with `.\scripts\start.ps1 -ServerName close_test`. **Admin token never goes in this file** (loader hard-errors if it finds one), so this yaml is safe to share with friends to add to their lists.
2. **Room lobby** (classic SC2 style): each person takes a slot, you can change your **race** and click [Ready]; the **first person to join is the host** and can add computer players, remove slots, and click [Start Game] (requires all players ready).
   - Two human players = pure 1v1 (SC2 engine restriction: you **cannot** add a computer in a two-human game)
   - One human + computer = original single-player mode, unchanged
3. After starting, wait about 30 seconds (two SC2 instances boot up), then each phone enters the familiar commander interface — the screen, minimap, and commands each belong only to your own bot. Disconnecting and reconnecting doesn't drop your slot or interrupt the game.
4. Friend connection: same WiFi — just enter the PC's LAN address; different networks — install Tailscale and use the 100.x address (see README/CLAUDE.md "PWA connection" section).

<!-- chat-skip-end -->

### 2. Mobile UI Overview

Top to bottom:
- **Top bar**: VibeCraft brand on the left + three-segment status chain (Connection / SC2 / Bot) on the right
- **Live game view** (top in portrait / left in landscape): the SC2 battlefield is streamed live to your phone — **this is what you watch to play**, no need to look at the PC
- **Minimap + touchpad**: drag the minimap view frame to pan the camera (your phone stream follows); the touchpad for fine adjustments
- **Right "Current Macro Strategy" (L1)**: build name + source tag (player / bot auto / default) + phase progress. **x in the top-right to cancel the current build**
- **BOT Current Decision**: attacking / defending / expanding / scouting / macroing + short reason
- **Command list (L2 / L3 / L4 unified card stack)**: each card has:
  - Layer label (L2/L3/L4) + plain-language description ("train Zealot x2 / Stalker x3", "DT scout enemy_main")
  - **Status color**: orange = waiting for condition / green = executing / gray = pending / semi-transparent = done (done cards auto-disappear)
  - **Condition checklist**: `o train 2 Zealots [0/2]` / `+ train 3 Stalkers [3/3]`, counter-type with progress numbers
  - x in top-right to cancel
- **Macro panel**: one-tap economy control, no need to speak —
  - **"Expand +1" button**: tap to send workers to open the next base (current base count +1), sending an expansion card that auto-disappears once the base is built. Tap again to open another.
  - **"Mining"**: Minerals-first (saturate minerals, extras go to gas) / Gas-first (fill gas 3/geyser, rest on minerals) / Default (bot decides). **Only matters when workers are scarce** (with plenty of workers, minerals are already full and extras go to gas anyway).
  - **"Workers"**: Stop (no more workers) / Fill (to saturation) / Default.
- **Recent commands**: your recent spoken commands
- **Input box** (pinned at bottom): long-press to bring up the phone's system keyboard with speech input / type directly

### 3. Input Methods

**Core = the text box**. Type in the text box → tap to send to the bot. Two ways to fill the text box — **completely equivalent**, use whichever is comfortable:

**A: System keyboard speech-to-text** (fastest, great for calling tactics on the fly)
- Long-press anywhere below the text box to bring up the phone's system keyboard → start recording
- Release → the keyboard converts speech to text and fills the text box
- Bad transcription? The text box is editable — fix it inline
- Fixed → tap send

**B: Direct typing** (good for complex pre-planned commands)
- The text box is always editable
- Type it in advance, wait for the cooldown, then tap send

> **VibeCraft does no speech recognition itself** — recording and transcription are done entirely by your phone's system keyboard (iOS built-in / Gboard / any other input method). Accuracy depends on your keyboard, but **accuracy doesn't matter** — you can always edit the text box after transcription before sending.

**Panning the camera: drag the minimap**
- The rectangle on the minimap = the current SC2 camera view
- Drag it → the SC2 screen follows
- Doesn't consume cooldown — pan as much as you want

**Undo: 1.5-second window**
- After sending a command, the recent commands area shows an [undo] button for 1.5 seconds
- If you miss it, you can only override with another command

### 4. Can I Use the Keyboard and Mouse on the PC?

**Yes, but not recommended**. You're in a player slot — keyboard/mouse actions fight with the AI for the same player slot, causing 1-2 frame jitter (quickly self-correcting).

The intended setup: **lean back on the couch, phone in hand** — the game view, minimap, commands, and battle status are all on your phone; the PC just sits off to the side running SC2, and you never have to touch it or watch it. (If you want a bigger, sharper picture you can glance at the PC screen, but you don't have to.)

### 5. Language / English

The PWA has a **ZH/EN toggle** in the top-right corner (entry page, lobby, cockpit header) that takes effect immediately and is persisted. After switching:

- **Full English UI**: all buttons, labels, toasts, status text.
- **Voice / text commands fully in English**: speak or type commands in English, the LLM parses in English and returns English interpretations.
  - "build two gateways" → adds 2 Gateways to build
  - "attack their main with everything" → all-in attack on enemy main
  - "retreat all units back home" → full army retreat to main base
- **English speech recognition**: decoded all at once after you release (not real-time partial); "Recognizing..." shown while recording.
- Buildings still use hotkey abbreviations (BG / BE / VS etc., layout-based regardless of language), units use official SC2 English names (Stalker / Immortal ...).

**Admin must pre-fetch the English ASR model before first use** (`scripts/prefetch_asr_en.py`, ~1 GB, ~6 minutes first time). Model is cached after download; subsequent loads are instant. If skipped, the first English-speaking player's first sentence will be delayed by the model download.

---

## IV. Supported Macro Builds

Categorized by race and game phase. **Green = supported, Yellow = coming in a future version**.

---

### Protoss

#### Opening Builds (0-5 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **1-Gate Robo Immortal** (1G Robo Immortal) | Most stable, all-purpose, works against all races |
| Green | **4BG Gateway Pressure** (4 Gateway Pressure) | Early aggression, grab tempo, good vs mirrors / Terran |
| Yellow | Double-gas Stargate Phoenix opener | Classic PvZ opener |
| Yellow | Fast DT rush | Single-base Dark Templar sneak attack |
| Yellow | Double-gas Oracle harass | Classic PvT opener |
| Yellow | Nexus First greedy economy | Current-meta greedy econ opener vs Zerg |

#### Mid-game Builds (5-12 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Double-base IAC ground** (Immortal/Archon/Chargelot) | Slow push standard, Zealot/Archon/Immortal push |
| Yellow | Double-base Blink Stalker timing | 7:00 Blink timing attack |
| Yellow | Phoenix harass into three-base | Air-control macro route |
| Yellow | Immortal/Sentry push | 7-9:30 timing attack |
| Yellow | Prism DT harass | Multi-pronged harassment to drain economy |
| Yellow | Double Void Ray + Stalker | Air superiority pressure |
| Yellow | 9 BG Warp-in all-in | No-expansion pure one-shot |
| Yellow | Glaive Adept push | Resonating Glaives Adept harass + push |

#### Late-game Builds (12+ min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Skytoss Carrier** | All-purpose backstop, slow DPS push |
| Yellow | Immortal Protoss (IAC + HT) | Heavy ground + Storm |
| Yellow | Mothership + Carrier | Mobility dominance |
| Yellow | Disruptor heavy ground | Standard PvT anti-bio answer |
| Yellow | Tempest long-range pressure | Slowly demolish turtle positions |

#### Protoss Command Examples

| You say | Bot does |
|---|---|
| "4BG pressure" / "4-gate" | Switch to 4BG pressure |
| "1-gate VR / fast Immortal" | Switch to 1-Gate Robo Immortal |
| "IAC / heavy ground" | Switch to IAC mid-game |
| "Skytoss / go Carriers" | Switch to Skytoss late-game |

---

### Zerg

Bot defaults to **12pool** opener, scouting with an Overlord. You can switch to mid/late builds at any time.

#### Opening Builds (0-5 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **12pool** | Early Spawning Pool for initiative; early Zergling harassment |
| Green | **Macro hatch** | Hatchery first, then Pool — economic lead route |

#### Mid-game Builds (5-12 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Roach + Hydralisk (roach_hydra)** | Two-unit ground army, strong vs bio / ground pressure |
| Green | **Mutalisk harass (mutalisk_harass)** | Fast Mutalisk harassment to drain opponent economy |

#### Late-game Builds (12+ min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Corruptor + Brood Lord (brood_corruptor)** | Upgrade Greater Spire into Brood Lords, all-purpose finisher |

#### Zerg Command Examples

| You say | Bot does |
|---|---|
| "12pool / fast BS" | Switch to 12pool opener |
| "macro hatch / hatch first" | Switch to macro_hatch economic opener |
| "vs Protoss macro / ZvP macro / spore macro" | Switch to zvp_macro (hatch-first fast three-base + spore anti-air, Protoss-specific) |
| "switch Roach Hydra / go Roaches" | Switch to roach_hydra mid-game |
| "Mutalisk harass / go Mutas" | Switch to mutalisk_harass harassment route |
| "go Brood Lords / BL finisher" | Switch to brood_corruptor late-game |
| "Zergling all-in" | Zergling rush (via 12pool + L2 attack command) |
| "make 10 Banelings" | Train 10 Banelings (L4 override) |

---

### Terran

Bot defaults to **marine_rush** opener, scouting with an SCV.

#### Opening Builds (0-5 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Marine rush (marine_rush)** | Fast 3-barracks all-in, grab early tempo |
| Green | **Reaper expand (reaper_expand)** | Reaper harassment + stable double expansion |

#### Mid-game Builds (5-12 min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Bio Stimpack (bio_stim)** | Marine + Marauder + Medivac trio, 8:00-9:30 timing |
| Green | **Double-base tank push (two_base_tanks)** | Tank siege + Marine escort, strong vs Roach / Immortal |

#### Late-game Builds (12+ min)

| Status | Build | Use Case |
|---|---|---|
| Green | **Battlecruiser (bc_late)** | Fusion Core + Battlecruisers, all-purpose finisher |

#### Terran Command Examples

| You say | Bot does |
|---|---|
| "Marine rush / fast BB" | Switch to marine_rush opener |
| "Reaper expand / Reaper opener" | Switch to reaper_expand economic opener |
| "bio / MMM timing" | Switch to bio_stim mid-game |
| "tank push / double-base tanks" | Switch to two_base_tanks mid-game |
| "go Battlecruisers / BC finisher" | Switch to bc_late late-game |
| "make 4 tanks" | Train 4 SiegeTanks (L4 override) |
| "research Stimpack" | Prioritize Stimpack research (L4 override) |
| "teleport all Battlecruisers back" / "tactical jump home" | Selected Battlecruisers use Tactical Jump to **instantly** warp back to main base (not move) |
| "all Battlecruisers go harass" / "BCs harass" | All Battlecruisers form a **harassment group** to harass (auto-selects mineral lines without anti-air) |
| "3 Battlecruisers harass 2nd base" / "2 BCs harass main" | Harassment group uses only N ships, targeting the specified base (main/2nd/3rd) |
| "reduce BC harassment to 2" / "pull back harass keep 2" | Group shrinks to N ships, extras rejoin main army (prioritizes full-health home BCs) |
| "stop BC harassment" / "BCs stop burning" | All return to main army, factory paused (card remains, x to fully delete) |

**Battlecruiser group harassment** (bc_rush fast-BC build comes with this; reworked 2026-06-29): all Battlecruisers are controlled by **a single group command**, coordinated as a team — one "**BC Harassment Group xN**" card (N = current controlled ship count). Behavior:
- **Leave together when strength is sufficient**: full-health BCs group up and surge out together; damaged ones return to repair, rejoin the next wave as a group (no more lone-ship suicide runs).
- **Hug the map edge**: fly along map boundaries to approach enemy mineral lines from behind, spotted later, get more kills.
- **Self-preservation over kills**: if enemy anti-air or large army arrives, **pull to outside attack range first**, don't stick to workers while taking hits; instantly destroy isolated spore crawlers; if a defended base can't be killed, shift the whole group to an undefended one.
- x "BC Harassment Group" card → all Battlecruisers **rejoin main army** (group up with Marines), harassment stops.
- To send a lone ship on an independent raid: "send one Battlecruiser to harass their 4th base".

---

## V. Supported Micro Types (by the 4 Layers)

### L1 Macro Strategy (Switch Build)

**Most frequent command**. Switching to another build takes effect immediately. The new build takes over production and decision-making for the corresponding phase.

- "4BG pressure" / "switch to IAC" / "go Skytoss"
- Cancel: click x on the strategy card (but usually you don't cancel — you just switch directly to the next build)

### L2 Tactical Commands (Army Direction, No Specific Units)

**Two types:**

**Type A (persists until cancelled)**: `attack / defend / retreat / vision`
- "attack 2nd base", "full defend base", "full army retreat to base", "watch enemy main"
- No auto-complete condition — stays active until you click x

**Type B (has completion condition)**: `scout / harass / drop / raze / regroup / split / expand`
- "Phoenix kill 5 enemy workers then return" → ends automatically when kill count is reached
- "retreat in 30 seconds" → ends when countdown expires

Cancel: card x button.

### L3 Unit Standing / One-Shot Tasks

#### a. One-shot takeover (auto-returns when task complete)
- "Void Ray lift that Immortal", "High Templar cast Storm", "DT sneak into their base"
- "send probe to 11 o'clock" → Probe walks to target, auto-returns to bot control

#### b. Standing Order (permanently claims units, persistent=true)
**Give a unit a permanent task — bot will never touch it until you cancel:**
- "that Zealot hold here don't move"
- "Sentry block the ramp, Force Field if enemies come"
- "DT guard their gas building"
- "3 Phoenix patrol 1st-2nd base line"

Cancel: card x or say "that Zealot come back".

#### c. Reposition / Move
- "Stalkers rally at main ramp", "full army come home", "push to map center"

#### d. Build at Specified Location
- "Pylon at 11 o'clock", "build a BG at their natural choke"

#### e. Scouting
- "send probe to 11 o'clock to look", "fly Observer over to their main"

### L4 Production Override (**Auto-disappears on completion**)

**Key semantic**: you say "make 4 Zealots" → card shows `[0/4]` progress → all 4 trained → card **auto-disappears**. Click x if you want to cancel early.

#### Train Units (supports multi-unit in one sentence)
- "train 2 Sentries"
- **"make 2 Zealots and 3 Stalkers"** → one card, two progress bars, **disappears when BOTH are done**
- Note: "make 1 Zealot" disappears on completion; to keep training say "keep making Zealots" or keep issuing new commands

#### Research Tech
- "research Blink first", "get Storm", "upgrade attack 1", "upgrade shields"

#### Expand
- "expand to 3rd now" / "take a new base"
- "delay 2nd base" (postpone until a condition)

#### Add Buildings (supports multi-building in one sentence)
- "add up to 8 BG at main"
- **"ramp: 2 cannons 1 BF"** → one card, two tasks, **disappears when ALL are built**

#### Salvage / Sell Buildings (Terran, recover some minerals)
- "sell the bunker" / "salvage that bunker" / "tear down the bunker"
- One-shot action, bot auto-selects the correct salvage ability (bunker / sensor tower); cannot salvage ineligible buildings — friendly message, no error.
- Only works on **your own** buildings.

### Camera Selection ("units in view...")

**First pan the camera to the target area on the minimap, then say "units in view / on screen / visible \<X\> do something"** — bot only acts on the units/buildings **visible in your camera at that moment** (won't affect off-screen units of the same type):
- "group all Stalkers in view into group 2"
- "all units on screen attack here"
- "all bunkers on screen salvage"

Different from "here/this spot" (a **landing point**) — "units in view X" is **area selection**; the two can be combined ("units in view attack here" = select visible units + camera landmark). Must specify the unit/building type or "units" (army); can't just say "select everything in view".

### Commands That Don't Go Into the Card Stack (UI actions)

- **Camera pan**: "look at their base", "cut to the main front" (also just drag the minimap — no cooldown)
- **Cancel**: [undo] button within 1.5 seconds; after that click the x on the specific card

---

## VI. Command Examples (by Purpose)

### Switch Build

| You say | Bot does |
|---|---|
| "4BG pressure" / "4-gate pressure" / "4 gateway Stalkers" | Switch to 4BG pressure |
| "go stable" / "1-gate VR" / "fast Immortal" | Switch to 1-Gate Robo Immortal |
| "switch to IAC" / "heavy ground" / "Zealot Archon Immortal push" | Switch to IAC mid-game |
| "Skytoss fallback" / "go Carriers" / "Carrier finisher" | Switch to Skytoss late-game |
| "Blink timing attack" / "Blink Stalker timing" | Switch to double-base Blink timing |
| "switch Phoenix macro" / "go Phoenix" / "Phoenix economy" | Switch to double-base Phoenix |

### Train Units / Research Tech (L4, auto-disappears on completion)

| You say | Bot does | Card |
|---|---|---|
| "next BG train 2 Sentries" | Gateway trains 2 Sentries | 1 card, disappears when done |
| **"make 2 Zealots and 3 Stalkers"** | Gateway trains 2 Zealots + 3 Stalkers | **1 card with 2 progress bars, disappears when both done** |
| "add one more Immortal" | Robo trains 1 Immortal | 1 card, disappears when done |
| "pause production" / "stop training" | All BG paused | — |
| "research Blink" / "get Blink" | Prioritize Blink research | 1 card, disappears when done |
| "upgrade shields" / "shield 1" / "upgrade attack and armor" | Prioritize Shield / Armor upgrade | 1 card |
| "get Storm" / "research Psi Storm" | Prioritize Psionic Storm research | 1 card |
| "that VR switch to Observer" | Specified VR next produces Observer | 1 card |

### Minerals / Expansion

| You say | Bot does |
|---|---|
| "expand to 3rd now" / "take a base" / "expand" | Expand immediately |
| "delay 2nd base" / "no rush to expand" | Postpone next expansion |
| "no expansion" / "all-in" / "forget the mining" | Cancel all expansion plans |
| "stabilize then expand" | Delay expansion to specified condition |

### Attack / Defense Constraints

| You say | Bot does |
|---|---|
| "push out" / "press forward" / "attack" | Army attack-move |
| "defend" / "come back" / "turtle up" / "don't engage" | Army retreat to main base |
| "contain their 2nd" / "block their expansion" | Don't attack, patrol their natural |
| "harass first, don't commit" | No direct engagement, harass only |
| "don't attack yet, wait for Blink" | Delay attack until condition is met |

### Unit One-Shot Tasks

| You say | Bot does |
|---|---|
| "Phoenix lift that Immortal" | Phoenix Graviton Beam targets enemy Immortal |
| "DT sneak attack" / "DTs go raid their mineral line" | DT sneaks to enemy mineral line to kill workers |
| "Warp Prism pick up 2 Immortals and drop" | Warp Prism loads Immortals, drops at target point |
| "High Templar Storm here" | HT casts Psionic Storm at specified position |
| "Mothership cloak here" | Mothership casts Cloaking Field at specified position |
| "Stalkers Blink out" | Selected Stalkers Blink retreat |

### Standing Orders (Long-term Tasks)

| You say | Bot does |
|---|---|
| "that Zealot hold here" | Zealot permanent hold position |
| "Sentry block the ramp, Force Field on enemies" | Sentry hold + reaction (FF on enemy_in_range) |
| "DT guard their gas, kill any workers you see" | DT hold + reaction (attack workers) |
| "Warp Prism hold at base entrance ready to pick up retreating units" | Warp Prism hold + reaction (pickup low_hp) |
| "Phoenix patrol between 1st and 2nd base" | Phoenix patrol between A and B |

### Cancel Standing Orders

| You say | Bot does |
|---|---|
| "that Zealot come back" | Release that Zealot, return to base bot |
| "all guards dismiss" | Release all units in hold state |
| "Phoenix stop, rejoin main army" | Release Phoenixes, enter army pool |
| "cancel all" | Dismiss all standing orders |
| "send 3 SCVs to repair the Battlecruiser" (Terran) | N SCVs repair target, auto-completes when full HP (persists until x) |
| "repair that bunker" / "repair all damaged tanks" | Repair command: Terran only, mechanical units/buildings only |
| Build an extra Command Center at home (Terran) | When expanding, bot will **preferentially fly this idle CC** to the new base — no new construction needed (idle CC flies over if available, otherwise builds new). CC must be idle (not producing SCVs) to fly |

### Standing / Factory Commands (auto-apply to units produced *later*)

Add words like **"from now on / later / every / always / keep / newly-made"** and the command **keeps applying to future units you produce** (not just the current batch). Each new unit off the production line gets the rule applied automatically — like setting a standing rule on the factory. Stops when you × the card.

| You say | Bot does |
|---|---|
| "put all future Stalkers in group 1" / "new Battlecruisers auto-join group 2" | **Auto-enroll group**: each newly-made unit of that type is added to group N (units already in stay; × stops auto-enroll, the group is kept) |
| "send all future units to rally here" / "set the rally point here" | **Rally point**: a global rally point — future units head here by default (doesn't move existing units or take control of them) |
| "all future Battlecruisers auto-harass" | **Standing harass**: newly-made BCs auto-join the harass group, hugging the map edge to hit the enemy mineral line (× stops) |
| "keep defending home from now on" / "hold defense until Blink is done" | **Standing stance**: defend/hold is kept — the bot doesn't disband after one wave (× or cancel to stop) |

> Distinction: "gather **these current** X units somewhere" only affects **existing** units (one-shot); it takes "from now on / new / future" to make it a **standing factory command** that applies going forward.

### Scouting

| You say | Bot does |
|---|---|
| "send probe to 11 o'clock" / "scout 11 o'clock" | Send a Probe to scout |
| "see what they're doing" / "scout their base" | Scout enemy main |
| "fly Observer to their main" | Send Observer to scout |
| "Phoenix scout a bit" | Send Phoenix to scout |
| "check that expansion" | Scout specified expansion |

#### Quick Scout Shortcuts

| You say | Bot does |
|---|---|
| **"scout expansions"** / "check if they expanded" / "check their expos" | Send 2 cheapest idle units to **separately** check enemy 2nd + 3rd base, take a look and return (light scout) |
| **"force-scout expansions"** / "scout with combat units" / "armed scout their expos" | Send 4 combat units to enemy 2nd base (recon squad), capable of handling an intercept; auto-retreat when vision acquired / losses exceed 40% / 30 seconds — whichever comes first |

**Light scout** is good for a quick look at whether they expanded, low risk, doesn't drain combat power.
**Force scout** is for when the enemy might have defenders; small combat squad handles an intercept, at the cost of 4 combat units.
Both scout types are **manually triggered** — the player calls the command, no auto-switching.

### Repositioning

| You say | Bot does |
|---|---|
| "Stalkers rally at main ramp" | All Stalkers → main_ramp |
| "push to map center" | Army → map_center |
| "come home" / "retreat" | Army → main_base |
| "go demolish that building" | Army → target_building, attack |
| "guard 2nd base" | Army → 2nd_base |

### Building Locations (L3 BUILD_AT specified point / L4 STRUCTURE_OVERRIDE count target)

**L3 Specified point, one-shot** (auto-returns to base bot when done):

| You say | Bot does |
|---|---|
| "Pylon at 11 o'clock" | Build Pylon @ 11_oclock |
| "BG at their natural choke" | Build Gateway @ enemy_natural_choke |
| "cannon at the ramp" | Build Photon Cannon @ main_ramp |
| "Pylon in the middle" | Build Pylon @ map_center |

**L4 Count target** (auto-disappears when done, supports multi-building in one sentence):

| You say | Bot does | Card |
|---|---|---|
| "add up to 8 BG at main" | Build Gateways at main until total is 8 | 1 card, disappears when total reaches 8 |
| **"ramp: 2 cannons 1 BF"** | Ramp: 2 Photon Cannons + 1 Forge | **1 card with 2 progress bars, disappears when ALL done** |
| "2 BE at 2nd base" | Build 2 Pylons at 2nd base | 1 card |

### Camera (No Rate Limit)

| You say | Bot does |
|---|---|
| "look at their base" | view_move → enemy_main |
| "cut to the main front" | view_move → current_battle |
| "follow the Mothership" | view_follow → mothership |
| "back to main base" | view_move → own_main |

### Compound Commands (Multiple Actions in One Sentence)

| You say | Bot does |
|---|---|
| "4BG pressure, if it fails go double expansion" | Switch 4BG + set abort to 2-base |
| "switch Phoenix, lift their workers when Phoenixes are ready" | Switch Phoenix + harass when units are ready |
| "Blink Stalker timing, contain their 2nd" | Switch Blink Stalkers + no attack + contain natural |
| "defend, produce Sentries to block the choke if they come" | defend + Sentry production condition + FF reaction |
| "go stable 1-gate VR, research Blink first" | Switch 1-gate VR + Blink priority |

---

## VII. Building / Unit / Tech Alias Quick Reference

### Buildings

| Abbreviation / Alias | Full Name |
|---|---|
| Pylon / power | Pylon |
| **BG** / Gateway | Gateway |
| gas building | Assimilator |
| nexus / main base | Nexus |
| **BF** / Forge | Forge |
| cannon | Photon Cannon |
| battery | Shield Battery |
| **BY** / core | Cybernetics Core |
| **VC** / council | Twilight Council |
| **VR** / Robo / robotics | Robotics Facility |
| **VS** / Stargate / gate | Stargate |
| **VT** / archives / Templar archives | Templar Archives |
| **VB** / bay / Robo Bay | Robotics Bay |
| **VF** / beacon / Fleet Beacon | Fleet Beacon |
| **VD** / Dark Shrine | Dark Shrine |

### Units

| Abbreviation / Alias | Full Name |
|---|---|
| probe / worker | Probe |
| Zealot | Zealot |
| Stalker | Stalker |
| Sentry | Sentry |
| Adept | Adept |
| **HT** / High Templar | High Templar |
| **DT** / Dark Templar | Dark Templar |
| Archon | Archon |
| Immortal | Immortal |
| Colossus | Colossus |
| Disruptor | Disruptor |
| Obs / Observer | Observer |
| WP / Warp Prism | Warp Prism |
| Phoenix | Phoenix |
| Oracle | Oracle |
| Void Ray / **VR** (semantic) | Void Ray |
| Carrier | Carrier |
| Tempest | Tempest |
| Mothership | Mothership |

> **VR ambiguity**: in a "build VR" context it means Robotics Facility (building); in "train VR" or "make VR" context it means Void Ray (unit). The bot disambiguates by context.

### Tech / Upgrades

| Abbreviation / Alias | Full Name |
|---|---|
| warp gate / WG | Warp Gate |
| Blink | Blink |
| Glaives / attack speed | Resonating Glaives (Adept attack speed) |
| Charge | Charge (Zealot) |
| Storm | Psionic Storm (HT) |
| Feedback | Feedback (HT) |
| attack 1/2/3, armor 1/2/3, shield 1/2/3 | Each level of attack/armor/shield upgrades |

---

## VIII. Frequently Asked Questions

### Q: The bot isn't following my command — what do I do?

Check the recent commands area to see how the AI interpreted it:
- Green checkmark + correct interpretation text → already executed, may take a few seconds to see results
- Ellipsis → still executing (e.g. waiting for units to be trained)
- Red X → parse failed, read the error and try again with different wording

### Q: I said something wrong or the bot misunderstood — what do I do?

Click undo within 1.5 seconds. After 1.5 seconds, send a new command to override.

### Q: The speech recognition is inaccurate — what do I do?

VibeCraft does no speech recognition itself — recording and transcription are done entirely by your phone's system keyboard. The text box is editable: **long-press to record → system keyboard transcribes → edit it yourself → send**. Don't try to say it perfectly the first time; edit after transcription — it's less mental effort.

### Q: My units are being pulled away by the base bot — what do I do?

Give them a Standing Order ("XX hold here"), and they become exclusively yours — the base bot can't touch them.

### Q: The 10-second cooldown is too long

That's core product design — it forces you to think before you speak. You can **type ahead of time** and fire when the moment is right.

### Q: Can I use the mouse and keyboard on the PC?

Yes, but not recommended. Easy to fight with the bot for control. The design intent is phone control — just push the keyboard and mouse out of reach.

### Q: I tapped the send button on my phone but nothing happened?

Check the countdown — you're still in cooldown. One command per 10 seconds.

### Q: How do I know the bot understood me?

Check the recent commands area; each entry shows:
- Green check / ellipsis / red X status
- AI's plain-language interpretation ("I understand this as X")
- [undo] button for 1.5 seconds

### Q: What difficulty AI can the bot beat on its own?

With no player input, ~50% win rate vs Hard AI; active participation wins Hard; deep engagement wins Harder.

### Q: How many races are supported?

All three (Protoss / Zerg / Terran). Which race you play this game is set when the match starts.

### Q: Can I play multiplayer?

MVP is vs built-in AI. Two-laptop PvP is supported from v1.1+.

### Q: Does parsing require internet?

Yes. LLM parsing uses cloud APIs (Claude / DeepSeek). No internet means no parsing. Local model support is planned for a future version.

---

## IX. Tips for Veteran Players

1. **Use SC2 jargon directly** — "4BG", "double-base Phoenix", "Blink timing", "DT sneak attack", "Carrier finisher" — the bot understands, no need to simplify
2. **Buildings: use abbreviations or English full names** — BG/VC/VR/VS or "Gateway"/"Stargate" — both recognized, use whatever's natural
3. **Compound commands in one sentence saves cooldown** — "4BG, fail → double expansion, defend" is three things in one command — saves 20 seconds vs three separate commands
4. **Multi-unit / multi-building in one sentence merges into one card** — "make 2 Zealots + 3 Stalkers" / "ramp: 2 cannons 1 BF" is **one card**, disappears when everything is done; say them separately if you want to track or cancel them individually
5. **Put key units on Standing Orders** — your command intent is permanently preserved; bot won't forget
6. **Cockpit has a "Bot Decision" area — watch what it's thinking** — disagree with its judgment? Just say so
7. **Don't say the obvious** — probe production / Pylon / basic production: the bot handles it, don't waste cooldown
8. **Call out strategic moments** — "push out", "expand", "get Storm" — bot defaults to conservative, won't move without being told
9. **The tighter the timing, the shorter the command** — "retreat", "push", "block the ramp", "storm them" — the bot understands shorthand
10. **To cancel a recent command** — [undo] within 1.5 seconds; after that click x on the specific card's top-right corner
11. **Long-term tasks can stack** — a Zealot holding the ramp + a DT guarding their gas + Phoenixes patrolling the 1-2 base line — no conflicts
12. **L4 card progress tells you where production is** — no need to switch back to SC2: "make 4 Zealots" currently at [2/4]

---

## X. Common Mistakes

| Don't say | Say instead |
|---|---|
| "Win this game for me!" | (bot doesn't understand "win" — give specific tactics) |
| "Control that enemy Immortal" | Cannot control enemy units |
| "Make a Zerg Mutalisk" | Bot is Protoss, can't make Zerg units |
| "Umm... that... you know..." | Just say the tactic name directly, no hesitation |
| Slow, sentence-by-sentence commands spread over multiple messages | Say all your intent in one sentence |
| Wait for the bot to attack first | Default is conservative — say "push out" or it won't move |

---

<!-- chat-skip-start -->
## XI. Version History

| Version | Contents |
|---|---|
| MVP (v0.1) | Protoss, 3 builds (1-Gate Robo + IAC + Skytoss), vs SC2 built-in AI |
| v0.5 | Protoss 8+ builds, more micro |
| v0.6 (M6) | All three races: Zerg 5 builds + Terran 5 builds; `--my-race` CLI; VibeCraftBotBase class |
| v1.0 | Full Protoss, two-laptop PvP |
| v1.5 | 10+ builds per race, PWA race selector |
| v2.0 | Player-narrated build generation, local LLM |

---
<!-- chat-skip-end -->

*VibeCraft — bringing veterans back to the Koprulu Sector, one command at a time.*
