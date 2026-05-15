# vendor/sharpy — ATTRIBUTION

## Upstream

- **Project**: sharpy-sc2 (DrInfy's sharpy)
- **URL**: https://github.com/DrInfy/sharpy-sc2
- **License**: MIT (see LICENSE)
- **Cloned commit**: `d9577a0`
- **Clone date**: 2026-05-16

## Why vendored

sharpy-sc2 is not on PyPI. We vendor it to:
1. Pin an exact commit for reproducibility
2. Apply minimal shims without forking the upstream repo

## Import path shims (M1)

No cython_extensions or CombatBehavior patch needed — sharpy is pure Python.

The only sys.path requirement:
- `vendor/sharpy/` must be on `sys.path` so that:
  - `import sharpy.*` resolves from this directory
  - `from config import get_config` resolves to `vendor/sharpy/config.py`

Both are handled by `_ensure_sharpy_on_path()` in
`src/voicecraft/bot/auto_combat/protoss/bot.py`.

## M1 integration notes

- KnowledgeBot.on_start initializes all Managers (roles, unit_cache, pathing, etc.)
- KnowledgeBot.on_step: knowledge.update → execute → knowledge.post_update
- UnitRoleManager.set_task(task, unit) is the role API (takes Unit object, not tag)
- UnitTask.Reserved(8) is used for LLM_CONTROLLED units (framework doesn't touch Reserved)
- create_plan() is abstract — M1 returns empty BuildOrder (M3 will add IfElse tree)
