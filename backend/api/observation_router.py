from fastapi import APIRouter
from pydantic import BaseModel
from services.scheduler import add_observation_to_queue, generate_scripts

router = APIRouter(prefix="/api/v1")

class ObservationRequest(BaseModel):
    target_name: str
    ra: str
    dec: str
    frames: int
    exposition: float
    filter: str
    mode: str
    guide: bool = True
    focus: bool = True

@router.post("/observations")
async def schedule_observation(request: ObservationRequest):
    add_observation_to_queue(request.model_dump())
    return {"status": "success", "message": f"Osservazione per {request.target_name} messa in coda."}

@router.post("/generate")
async def generate():
    await generate_scripts()
    return {"status": "success", "message": "Script generati e inviati a INDIGO"}