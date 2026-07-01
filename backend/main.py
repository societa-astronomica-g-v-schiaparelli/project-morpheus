from fastapi import FastAPI
from api.observation_router import router as obs_router

app = FastAPI(title="ASTRA - Project Morpheus")
app.include_router(obs_router)


@app.get("/")
async def root():
    return {"message": "Popolo di Striscia!"}