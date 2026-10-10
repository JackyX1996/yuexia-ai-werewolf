"""Run with: python3 -m uvicorn scripts.ui_preview:app --port 8787.

Uses an isolated temporary database and never calls live model APIs.
"""
import asyncio
import os
from pathlib import Path
import tempfile

os.environ.setdefault("WW_DB_URL", "sqlite+aiosqlite:///" + str(Path(tempfile.gettempdir()) / "ww-ui-preview.db"))

from ai import graph  # noqa: E402
from scripts.preview_client import PreviewClient  # noqa: E402

graph.get_llm_client = lambda _: PreviewClient()

from main import app  # noqa: E402
from engine.game_manager import GameManager  # noqa: E402
from engine import game_manager  # noqa: E402
from ai.model_configuration import ModelConnection, PROVIDER_URLS  # noqa: E402
from ai.model_pool import pick_match_models  # noqa: E402

app.state.ai_mode = "simulated"

# A fresh preview database needs no credentials or saved live connections.
game_manager.pick_connections = lambda count: [ModelConnection(
    id="preview-" + name, provider="bailian", model_name=name,
    api_key="preview-only", base_url=PROVIDER_URLS["bailian"],
) for name in pick_match_models(count)]


async def preview_pace(self, content):
    await asyncio.sleep(.35)


GameManager._pace_delay = preview_pace
