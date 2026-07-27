# Guide Bilingual + Chat Assistant Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add English version to the player guide (linked to UI language) and embed a DeepSeek chat assistant in guide.html.

**Architecture:** (1) guide.html gains zh/en content divs toggled by `?lang=` or localStorage; EntryView.vue passes current lang. (2) `GET /api/guide-chat?q=...&lang=...&h=...` calls DeepSeek via AsyncAnthropic; `process_request` becomes `async def` to support awaiting the LLM. Rate-limited at 20 req/min/IP via in-memory sliding window.

**Tech Stack:** Python asyncio, anthropic SDK (AsyncAnthropic), websockets 15+, Vue 3, vanilla JS.

---

### Task 1: Create USER_GUIDE_EN.md
**Files:** Create: `USER_GUIDE_EN.md`
English translation of USER_GUIDE.md. SC2 hotkey abbreviations kept (BG/BE/VS/etc.), units use official English names (Stalker/Immortal/Phoenix/etc.), SC2 jargon kept (4BG/IAC/Skytoss/etc.).

### Task 2: Make process_request async + add guide-chat endpoint
**Files:** Modify: `src/vibecraft/server/http.py`
- Change inner `process_request` to `async def`
- Add `llm_config` optional param to `make_process_request`
- Add in-memory rate limiter (20/min per IP, sliding window)
- Add `async def _serve_guide_chat(ws, request, llm_config)` 
- Add route `if raw_path == "/api/guide-chat":`
- Guide text cached at module level from USER_GUIDE.md / USER_GUIDE_EN.md

### Task 3: Update 4 test files for async process_request
**Files:** Modify: `tests/unit/test_server_http.py`, `tests/unit/test_server_http_api.py`, `tests/unit/test_server_info_api.py`, `tests/unit/test_admin_auth.py`
Add `import asyncio` and wrap `hook(ws, req)` → `asyncio.run(hook(ws, req))`

### Task 4: Create test_guide_chat.py
**Files:** Create: `tests/unit/test_guide_chat.py`
Test rate limiting (429 after >20 in 1s), empty question (400), mock LLM response.

### Task 5: Update guide.html bilingual + add chat widget
**Files:** Modify: `web/public/guide.html`
- JS lang detection (URL param → localStorage → browser)
- CSS: `[data-lang=zh] .en-content { display:none }` pattern
- zh/en content divs with full translations of all 17 sections
- Lang toggle button in header
- Chat widget at bottom: input + message list + loading + error

### Task 6: Update EntryView.vue guide link
**Files:** Modify: `web/src/components/EntryView.vue` line ~156
`href="/guide.html"` → `:href="\`/guide.html?lang=\${i18n.locale}\`"`

### Task 7: Run tests and build
```
uv run pytest tests/unit/test_server_http.py tests/unit/test_server_http_api.py tests/unit/test_server_info_api.py tests/unit/test_guide_chat.py -v
cd web && npm run build (PowerShell only)
```

### Task 8: Real DeepSeek verification
Test guide-chat endpoint with curl or fetch, verify: game Q answered in correct lang, off-topic Q politely refused, rate limit 429 triggered.
