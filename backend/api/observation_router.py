from fastapi import APIRouter
from pydantic import BaseModel, field_validator, model_validator
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

    # --- Campi di scheduling ---
    # Orario fisso (UTC): se presente, la prima posa parte a quest'ora e vince sui
    # vincoli soft. Se assente, lo scheduling e' libero.
    fixed_start: str | None = None
    # Vincoli soft, ammessi SOLO in scheduling libero (checkbox utente):
    min_altitude: float | None = None   # altezza minima di comodita' (gradi)
    moon_check: bool = False            # attiva il vincolo di distanza dalla Luna
    moon_base_angle: float = 90         # ampiezza max della "zona vietata" attorno alla Luna
    # Osservazioni lunghe da spezzare su piu' notti:
    splittable: bool = False

    @field_validator("ra", "dec", mode="before")
    @classmethod
    def _to_decimal(cls, value, info):
        return sexagesimal_to_decimal(value, info.field_name)

    @model_validator(mode="after")
    def _fixed_excludes_soft(self):
        # come da appunti: i vincoli soft (altezza minima, Luna) sono disponibili
        # SOLO se l'orario non e' fisso.
        if self.fixed_start and (self.min_altitude is not None or self.moon_check):
            raise ValueError(
                "min_altitude e moon_check non sono ammessi con un orario fisso "
                "(l'orario fisso vince sui vincoli soft)."
            )
        return self

    def to_target(self):
        """Traduce la richiesta nel dict che si aspetta lo scheduler (target_name -> name),
        includendo solo i campi di scheduling effettivamente impostati."""
        target = {"name": self.target_name, "ra": self.ra, "dec": self.dec,
                  "frames": self.frames, "exposition": self.exposition}
        if self.fixed_start:
            target["fixed_start"] = self.fixed_start
        if self.min_altitude is not None:
            target["min_altitude"] = self.min_altitude
        if self.moon_check:
            target["moon_check"] = True
            target["moon_base_angle"] = self.moon_base_angle
        if self.splittable:
            target["splittable"] = True
        return target

@router.post("/observations")
async def schedule_observation(request: ObservationRequest):
    add_observation_to_queue(request.model_dump())
    return {"status": "success", "message": f"Osservazione per {request.target_name} messa in coda."}

@router.post("/generate")
async def generate():
    await generate_scripts()
    return {"status": "success", "message": "Script generati e inviati a INDIGO"}