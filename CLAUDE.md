# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                   # install (incl. dev group)
uv run python -m bot.main                 # run the bot (make run)
uv run pytest -q                          # full suite (make test adds coverage)
uv run pytest src/tests/test_stop.py -q   # one file
uv run pytest -k "test_split_message"      # one test
uv run ruff check src/ && uv run mypy src/ # make lint
uv run black src/ && uv run ruff check --fix src/   # make format
uv run alembic revision --autogenerate -m "name"    # make migration m="name"
```

`bot.config.Settings` is instantiated at import time, so `BOT_TOKEN` and `ADMIN_ID`
must be present or *collection itself* fails. Locally `.env` supplies them; CI
injects dummy values (`BOT_TOKEN=test-token ADMIN_ID=1`) before both ruff and pytest.

`pyproject.toml` sets `pythonpath = ["src"]`, so imports are `from bot...`, never `from src.bot...`.

## Architecture

Telegram bot (python-telegram-bot v22, polling) that proxies chat to a local
Ollama server, storing users and conversation history in SQLAlchemy async
(SQLite by default, PostgreSQL supported).

**Startup order matters** — `BotApplication.initialize()` in `src/bot/main.py`:
database → `load_into_settings()` → `OllamaClient` (+ `verify_connection`) →
PTB `Application` → handlers → command menu → health server. The Ollama client
and `settings` are handed to handlers through `application.bot_data`, not imported;
handlers read them via `context.application.bot_data["ollama_client"]`.

**Layers**

- `src/bot/handlers/` — one module per command family, all registered in
  `handlers/__init__.py:setup_handlers()`. The catch-all `MessageHandler`s must
  stay registered last.
- `src/bot/decorators.py` — `@authorized_only`, `@admin_only`, `@rate_limited`,
  `@log_command`. Order is `@authorized_only` then `@rate_limited`. Admin bypasses
  both authorization and rate limiting. Tests reach the undecorated function via
  `handler.__wrapped__`.
- `src/bot/utils/ollama.py` — `OllamaClient`: streaming `/api/chat`, model list
  cached 60s, per-model vision capability cached. Streaming uses
  `httpx.Timeout(self.timeout)` as a *per-operation* timeout deliberately — `None`
  there once meant `OLLAMA_TIMEOUT` never applied to generations.
- `src/bot/utils/context.py` — `ConversationContext` keeps a class-level LRU
  (`MAX_CACHED_CONTEXTS = 500`) of `deque`s. The cache is not the source of truth:
  an eviction just reloads from the DB. Anything that clears history must delete
  the `conversations` rows *and* call `forget()`/`clear()`, or the next message
  reloads the old conversation.
- `src/bot/utils/tokens.py` — history is trimmed to `MAX_CONTEXT_TOKENS` using a
  character heuristic biased to **overcount** (Ollama exposes no tokenizer).
  The last two messages are never trimmed.
- `src/bot/database/` — models, async engine/session, migration bootstrap.

**Cross-cutting invariants**

- **Ordering:** always `ORDER BY Conversation.id`, never `created_at` — SQLite
  timestamps have second resolution and same-second messages come back shuffled.
- **Sessions:** `get_session()` commits on clean exit and rolls back on exception.
  Do not add a trailing `session.commit()`; an explicit commit is only for
  flushing mid-block.
- **Concurrency:** PTB runs with `concurrent_updates(MAX_CONCURRENT_UPDATES)`, so
  handlers overlap. `chat.py` guards per-user generation with the module-level
  `_generating: dict[int, asyncio.Task]` — one generation per user, and `/stop`
  cancels through that dict. The `asyncio.CancelledError` branch re-raises without
  awaiting anything (the awaits would be cancelled too); `/stop` sends the
  confirmation itself.
- **HTML replies:** every interpolation of user text, model output or Ollama API
  fields into a `parse_mode="HTML"` message goes through `html.escape()` — a stray
  `<` makes Telegram reject the whole message. Model templates routinely contain them.
- **Message length:** replies over 4096 chars go through `split_message()`;
  intermediate streaming edits stop before the limit rather than failing.

**Migrations** run automatically at startup (`database/migrate.py`), not via a
manual alembic step. A database created by the old `create_all` path (tables
present, no alembic version) is stamped at baseline `0001` and then upgraded.
Alembic takes its URL from app settings in `migrations/env.py`; `alembic.ini` has
no `sqlalchemy.url`.

**Runtime settings:** `TEST_MODE` is toggled by `/test_mode` and persisted in the
`settings` table via `utils/runtime_settings.py`, then re-applied over the env
value at startup. Adding another admin-togglable flag follows the same pattern.

## Conventions

- Comments explain *why*, usually pointing at the bug the code prevents — keep
  that style; don't strip them when refactoring.
- User-facing bot strings are mixed Russian/English (newer text is Russian);
  match the surrounding handler.
- ruff/black line length 100; ruff lint set `E,F,W,I,N,B,UP`.
- Commits: Conventional Commits in Russian, no emoji.
