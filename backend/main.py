from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.observation_router import router as obs_router
from api.scheduler_router import router as sched_router
from db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()   # crea le tabelle del database all'avvio, se non esistono ancora
    yield       # da qui in poi l'app è viva; dopo lo yield andrebbe l'eventuale pulizia


app = FastAPI(title="ASTRA - Project Morpheus", lifespan=lifespan)
app.include_router(obs_router)
app.include_router(sched_router)

#   ->  http://<host>:8000/ui/
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")


@app.get("/")
async def root():
    return {"message": "Popolo di Striscia!"}