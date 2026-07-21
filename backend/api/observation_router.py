from fastapi import APIRouter
from pydantic import BaseModel, field_validator
from services.dispatcher import add_observation_to_queue, generate_scripts
from utils.coordinates import  sexagesimal_to_decimal

router = APIRouter(prefix="/api/v1")

class ObservationRequest(BaseModel):   
    target_name: str
    ra: float
    dec: float
    frames: int
    exposition: float
    filters: list[str]
    mode: str
    guide: bool = True
    focus: bool = True
    sequential: bool = False

    @field_validator("ra", "dec", mode="before")
    @classmethod
    def _to_decimal(cls, value, info):
        return sexagesimal_to_decimal(value, info.field_name)

@router.post("/observations")
async def schedule_observation(request: ObservationRequest):
    add_observation_to_queue(request.model_dump())
    return {"status": "success", "message": f"Osservazione per {request.target_name} messa in coda."}

@router.post("/generate")
async def generate():
    await generate_scripts()
    return {"status": "success", "message": "Script generati e inviati a INDIGO"}