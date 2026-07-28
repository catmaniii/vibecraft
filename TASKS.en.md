# TASKS — What's next

> **English** · [中文](TASKS.md)

**This file only covers work that hasn't been done yet.**
For what's already shipped, see [`CHANGELOG.md`](CHANGELOG.md); for how it works, see
[`ARCHITECTURE.md`](ARCHITECTURE.md); for why it was designed this way, see
[`docs/plans/2026-05-14-vibecraft-design.md`](docs/plans/2026-05-14-vibecraft-design.md)
(Chinese).

Want to pitch in? Read [`CONTRIBUTING.md`](CONTRIBUTING.md), then jump to
[Where to start](#where-to-start) at the bottom.

---

## Where things stand

It works, and it's rough. All three races play, you can issue everything from "switch build" to
"blink the stalkers in" by voice or text, the phone PWA shows live video, and multiplayer has been
verified on real hardware. Out of the box the bot beats Medium AI and goes roughly even with Hard.

| | Today |
|---|---|
| Build library | Protoss 9 openings + 8 doctrines, Terran 12 + 5, Zerg 9 + 5 (48 total) |
| Acceptance specs | 47 (`tests/build_acceptance/`) |
| Maps | **Only ever validated on DaybreakLE** (see Direction A) |
| Multiplayer | Multi-instance host/join, room lobby, public front door with cloud TURN relay |
| CI | Windows + Python 3.11, full 3731-test suite |

**The three biggest gaps are the three directions below.**

---

## Three directions

### Direction A: multi-map support — the biggest blocker right now

Everything has only ever been validated on `DaybreakLE`. Plenty breaks on another map, and the
hard part **isn't loading a different map** — python-sc2 handles that — it's **the commands that
are welded to terrain**:

- **Named spots move.** "the left watchtower", "their 11 o'clock expansion", "the ramp" sit at
  different coordinates on every map.
- **Signature builds have hardcoded placements.** The forward pylon for a 4-gate, proxy build
  sites, nydus landing spots, the hidden patches used by stealth mining — any of these can be
  invalid elsewhere.
- **Terrain reasoning is tuned on one map.** Low-ground routing and high-ground vision are
  implemented (`bot/terrain_harass.py`, `zerg/plans/nydus_landing_planner.py`), but their
  thresholds were fitted against a single map.

**The intended design** (decided, not built): one *map profile* per map — a table of signature
spots plus a prompt fragment injected into the LLM — loaded dynamically for whichever map is in
play. `NamedSpotRegistry` is already the right foundation; it needs to become per-map.

**Done means**: on at least three common ladder maps, `build_acceptance` and
`override_acceptance` pass at the same rate as on DaybreakLE, and commands like "the left
watchtower" resolve correctly everywhere.

---

### Direction B: keep tuning the build library, and keep up with the game

**Late-game doctrines are the weak spot**: Protoss has 8, Terran and Zerg only 5 each. Openings
are in decent shape — **the games that get away are usually lost in the mid-to-late game, with no
strong plan to switch into.**

There's also something **already out there that hasn't hit us yet**: **SC2 patch 5.0.16 — the
"8 worker start" — invalidates every build in this repo.**

It went live on 22 June 2026 and is the largest balance change in eleven years. The parts that
matter here:

| Change | From → To |
|---|---|
| **Starting workers** | **12 → 8** |
| Nexus / Command Center supply | 15 → 13 |
| Hatchery supply (cost) | 6 → 4 (275 → 300) |
| Large / small mineral patches | 1800 → 1600 / 900 → 1100 |
| Vespene geyser (total gas per base) | 2250 → 2500 (4500 → 5000) |
| Warpgate | Research now speeds Gateway production by 40%; transform costs 25/25; warp-in is a flat 4s |

**Why this is a full rewrite rather than a tweak**: every build here is written as
`<supply> build ...`. Starting with 8 workers and a town hall that only supplies 13 means **the
same supply number now describes a completely different game state** — `13 build BG` in the
4-gate used to mean "one unit after your 12 starting workers", and now means "five after your
8". All 48 builds and all 47 acceptance specs need recalibrating step by step. The Warpgate
rework separately shifts every Protoss warp-in timing (`4bg`, `iac_2base`, the chargelot
pipeline, and so on).

One prerequisite gotcha: `python-sc2` (burnysc2) only knows about game versions up to **5.0.14**
(base build 94137). Once the client updates to 5.0.16, its version table may need extending
before a game will launch at all.

**This is ongoing maintenance, not a one-off** — every future balance patch means another full
re-run.

**The workflow already exists** — follow
[`docs/process/new-opening-strategy.md`](docs/process/new-opening-strategy.md): research a real
pro build → write the YAML → tune with `build_acceptance` → run the six-dimension self-check
(described in `CLAUDE.md`). **You don't need to understand the codebase.** If you play SC2 and can
read a log, you can do this — it's the friendliest place to start.

**Done means**: 8+ late-game doctrines per race, and a repeatable process for re-running
everything after a patch.

---

### Direction C: many servers — volunteer machines plus a directory

Right now players can only connect to **one** host. Growing past that means someone else standing
up their own.

**The blocker is cost, not code.** Reaching a home PC from the public internet needs a VPS as a
front door and media relay, and WebRTC video is **sustained heavy traffic** — one maintainer's
account cannot relay for everyone.

**So the plan is to spread the cost out:**

- A volunteer supplies **their own Windows PC** (running SC2 + the server) **and their own VPS**
  (front door + TURN relay), and **pays for their own traffic**.
- The maintainer's VPS runs only a **server directory**: volunteer machines register themselves on
  startup, and players opening the site get a **list of servers** to choose from.
- **The directory itself is nearly free to run** — it only exchanges "which servers exist, are
  they up, what's their address". The expensive control and media planes stay on each host's own
  VPS. That's exactly what makes the cost sharing work.

**What to build**: the directory service (register / heartbeat / drop-off), a registration client
plus authentication on the volunteer side (so nobody can register junk), the PWA entry page
upgraded from a single server to a real list (`web/src/components/EntryView.vue` can already show
several, but the list is locally configured today), and a one-page deployment guide for
volunteers.

**Done means**: a stranger follows the guide, plugs in their own PC and VPS, and other players see
and connect to their server from the front page.

---

## Concrete tasks

Grouped by whether you need the game. 🎮 means you need a Windows machine with StarCraft II
installed; everything else is open to anyone.

### No SC2 required

- **Pay down the mypy debt.** `pyproject.toml` carries per-module `disable_error_code` overrides
  for 33 modules (154 of the errors are `union-attr` in `director.py` alone). Clear one, delete
  its entry; clear them all and the whole block goes away.
- **Fix the flaky cross-tests**: `test_loads_real_strategies`, `test_transitions_of`,
  `test_not_triggered_when_visible_but_insufficient_duration` — each passes alone and fails
  occasionally in the full run. Classic shared global state between tests.
- **`scripts/sync_to_opensource.py` is dead.** It implemented a private-repo → public-repo
  sanitising projection; vibecraft **is** the public repo now. It can simply be deleted.

### 🎮 SC2 required

- **Stealth mining for Terran and Zerg.** The system is hardcoded Protoss end to end
  (`StealthCellManager` assumes NEXUS/PROBE/ASSIMILATOR/chrono). Terran and Zerg players currently
  get a polite refusal. It needs a per-race abstraction: Zerg drone → hatchery → extractor (the
  drone is consumed, production runs on larva); Terran SCV → command centre → refinery (the SCV
  keeps building, MULEs exist). Each needs a self-test proving saturation.
- **Two builds still fail acceptance**:
  - `blink_stalker` 15/18 — 4-gate is slow and stalker production lags (6-7 units leaving home vs
    the spec's 10); the warp prism never gets built.
  - `iac_2base` 18/19 — only 2 DTs in the first batch (spec wants 4); a single warpgate rate-limits
    DT warp-ins. Careful: this build is tech-heavy and **adding gateways earlier starves the tech
    line** (measured) — the `dt_drop_iac` fix does not transfer.
- **Write the missing Zerg acceptance specs**: 12pool, macro_hatch, mutalisk_harass, roach_hydra,
  brood_corruptor and friends have none.
- **Deeper harassment micro**: widow mines unburrowing one at a time and hugging map edges; reaper
  solo control is high-variance.
- **End-to-end smoke**: one game per race vs VeryEasy.

---

## Known issues / debt

- **Acceptance checks have a built-in weakness.** Transient position checks (`dt_at_enemy`,
  `warp_prism_at_enemy`, `army_gather`) are judged from a single snapshot and are inherently
  flaky; the verifier's time-window logic only covers count-based checks so far.
- **Most features were validated by self-tests and screenshots**, not by accumulated real games
  against real people.
- **CI only covers Windows + Python 3.11** — not laziness, a hard constraint (see below).

---

## Open questions (undecided — input welcome)

### How strong should authentication be?

**Today** the only access control is a **room token**, and it travels in the URL
(`?room=<token>`) — anyone holding the link can command the bot and watch your screen. Forward
the link, screenshot it, paste it into a group chat, and you've handed over the key. (The admin
panel has a separate token; that one can change server settings.)

**It's tolerable right now** because servers spread by word of mouth: if you don't know the
address, you can't connect. But **Direction C removes exactly that cover** — once servers are
publicly discoverable, "nobody knows my address" stops being a defence.

**The options, and what each costs**:

| Approach | Upside | Cost |
|---|---|---|
| Keep it as is (one shared token) | Zero friction — send a friend a link and they're playing | The link *is* the key; you can't kick one person or attribute anything |
| One-time / expiring invite links | Stops a link being forwarded forever | The host has to mint and distribute one every time |
| Lightweight accounts (nickname + passphrase, or OAuth) | You can identify, kick, and log people | You're now storing user data — a real step up in responsibility and complexity |

**Leaning**: this is a tool for playing with friends, not a public service, and **over-engineering
auth could easily destroy its best property — open a link and you're in.** But it needs deciding
before Direction C ships. Thoughts welcome in an issue.

---

## Explicitly out of scope

So nobody wastes a weekend:

- **Other platforms (Linux / macOS).** The vendored sharpy's core managers import `sc2pathlib`
  unconditionally, and upstream ships exactly one build of it: `cp311-win_amd64`. Porting means
  first building sc2-pathlib (Rust) for your platform — that's a different project's problem.
- **Python 3.12+.** Same reason: that binary is cp311-only.
- **Moving the server to the cloud.** The server must live on the same machine as SC2 — it
  launches the game and captures its window by PID. That's an architectural invariant, not a task.
- **Ladder / ranked play.** This is an AI playing for you; using it on ladder violates the game's
  terms. It targets AI opponents and games among friends.
- **Local LLM inference.** Considered and dropped; the design commits to a cloud provider behind a
  swappable interface (see `docs/adr/0005`).

---

## Where to start

Three doors at different heights:

1. **You play SC2 and don't want to read code** → **Direction B**. Take a late-game plan you know
   well, write it as YAML following `docs/process/new-opening-strategy.md`, and tune it with
   `build_acceptance` until it passes. You'll only touch YAML and logs.
2. **You write Python but have no SC2** → the "No SC2 required" list. The mypy debt and the flaky
   tests are self-contained; `uv run pytest` is your whole feedback loop.
3. **You want something meaty** → **Direction A** (multi-map) or **Direction C** (server
   directory). Both start with `ARCHITECTURE.md`; A is terrain algorithms, C is distributed
   systems and deployment.

Open an issue before you start, so two people don't build the same thing.
