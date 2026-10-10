# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI 狼人杀 — a 12-player Werewolf game (4 wolves, 4 villagers, seer, witch, hunter, idiot) with 11 AI players + 1 human. Uses FastAPI, async SQLAlchemy, WebSocket for real-time play, and Bailian plus Volcengine models for AI decisions.

## Commands

```bash
# Run server
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Run all tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_rules.py -v

# Run a single test
python3 -m pytest tests/test_rules.py::TestCheckWinner::test_all_wolves_dead_good_wins -v
```

All commands must be run from the repository root.

## Architecture

```
API (api/routes.py, api/websocket.py)
  → GameManager (engine/game_manager.py)     # orchestrates full game loop as background asyncio task
    → State Machine (engine/state_machine.py) # 18-phase deterministic transitions
    → Rules (engine/rules.py)                 # win conditions (屠边), weighted vote counting
    → AI Decision (ai/graph.py)               # build_prompt → call_llm → validate → retry/fallback
      → LLM Client (ai/llm_client.py)         # DashScope OpenAI-compatible API
      → Jinja2 Templates (ai/prompts/)        # role-specific prompt templates
    → Repository (db/repository.py)           # CRUD + visibility-filtered event queries
      → ORM Models (models/)                  # Game, GamePlayer, GameEvent, PlayerMemory, WitchState
```

**Game loop**: `POST /api/games/{id}/start` spawns `GameManager.run_game()` as a background task. It cycles night→day phases, calling AI decisions for each AI player and prompting the human via WebSocket.

**Event visibility**: Every `GameEvent` has a visibility level (`all`, `werewolves`, `self`, `system`). `repository.get_visible_events()` filters events based on the requesting player's role — this is the core information asymmetry mechanism.

**AI decision flow**: `ai/graph.py:ai_decide()` renders a Jinja2 prompt template, calls the LLM in JSON mode, validates the response structure and target legality, makes at most 2 attempts, then falls back to a safe default action. Wolf night discussion makes multiple decisions with consensus checking.

**Match models**: The administrator manages persisted `ModelPlatformConfiguration` endpoints with multiple `ModelConfiguration` model entries from the lobby. `ai/model_configuration.py` samples enabled connections; fewer than 11 are repeated. Seats store `GamePlayer.model_config_id` for explicit provider routing and `model_name` for historical display/stats. Never persist a configuration ID as a model name. `ai/model_pool.py` only retains legacy import/preview names and call-shape compatibility. Thinking is fixed off and cannot be enabled through the page, API or runtime client; thinking-only models remain unavailable. Native modes use official endpoints; compatible mode uses an explicit Base URL and optional thinking parameter format.

**Kimi K3 compatibility**: Only supports thinking mode and fixed temperature 1.0. It cannot be enabled in the pool with Thinking off. Retained transport parsing tests bypass live-pool construction; never reintroduce a thinking opt-in. Its minimum output budget is 8192. Single and multi-turn calls share the same request policy in LLMClient._chat; truncated responses must not be adopted as decisions.

**Kimi latency**: Use streaming so the 45s read timeout means inactivity rather than waiting for the entire reasoning + answer. K3 does not support thinking_budget. Consume reasoning as progress only; accept final content only after finish_reason=stop. ai_decide bounds the complete decision including retries to WW_LLM_DECISION_TIMEOUT_SECONDS (120s). Safe metadata is persisted to a rotating data/ai-decisions.jsonl; never persist full prompts or raw provider error payloads there.

**Human interaction**: WebSocket at `/ws/games/{game_id}`. GameManager sends `prompt_action` messages and awaits human response via `asyncio.Future` with timeout. The `ConnectionManager` in `api/websocket.py` bridges WebSocket ↔ GameManager.

**Administrator accounts**: A fresh database seeds `WW_ADMIN_USERNAME` / `WW_ADMIN_PASSWORD`, defaulting to `admin / admin`, with `User.is_admin=True`. Blank values also use that default. Authorization and frontend controls use this role, never a username comparison. Existing installations retain their credentials. The one-time role-column migration promotes only the configured username and never resets passwords.

## Communication

- 永远使用中文回答用户的问题和进行交流。

## Key Constraints

- **Python 3.9**: Use `Optional[X]` and `List[X]` from `typing`, not `X | None` or `list[X]` syntax.
- **Tests stub the DB**: `tests/conftest.py` replaces `db.session` with a fake module before model imports, so tests run without a database connection.
- **Configuration**: Only operational settings (database, deadlines, token budgets) use `WW_` environment variables. Model lists and shared platform providers/keys come from the page/database; Thinking is fixed off. Legacy model environment variables are imported once when the configuration table is first created. Do not reintroduce runtime environment fallbacks or create an eager default LLM client at import time. Do not return API keys or echo invalid credential input/provider payloads in responses. Simulated previews never import real keys or run live connectivity probes.
- **LLM JSON mode**: All AI calls expect JSON responses with `inner_thought`, `speech`, and `action` fields. Templates must instruct the model to output this format.
