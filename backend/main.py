from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.observation_router import router as obs_router
from db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()   # crea le tabelle del database all'avvio, se non esistono ancora
    yield       # da qui in poi l'app è viva; dopo lo yield andrebbe l'eventuale pulizia


app = FastAPI(title="ASTRA - Project Morpheus", lifespan=lifespan)
app.include_router(obs_router)


@app.get("/")
async def root():
    return {"message": "Popolo di Striscia!"}