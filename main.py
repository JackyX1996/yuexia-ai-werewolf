from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import os

from db.session import init_db
from api.routes import router as api_router
from api.auth import router as auth_router
from api.websocket import router as ws_router
from api.model_configuration import router as model_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(import_legacy=getattr(app.state, "ai_mode", "live") != "simulated")
    yield


app = FastAPI(title="AI 狼人杀", version="0.1.0", lifespan=lifespan)
app.state.ai_mode = "live"

app.include_router(auth_router, prefix="/api")
app.include_router(api_router, prefix="/api")
app.include_router(model_router, prefix="/api")
app.include_router(ws_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # FastAPI's default 422 echoes the invalid input, which can contain API keys.
    messages = ["{0}: {1}".format(".".join(str(part) for part in item["loc"][1:]), item["msg"])
                for item in error.errors()]
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})

static_dir = os.path.join(os.path.dirname(__file__), "static")


@app.get("/game/{game_id}")
async def game_page(game_id: int):
    return FileResponse(os.path.join(static_dir, "index.html"))


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
