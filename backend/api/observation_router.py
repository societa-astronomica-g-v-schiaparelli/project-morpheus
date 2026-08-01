from fastapi import APIRouter
from pydantic import BaseModel, field_validator, model_validator
import config
from services.dispatcher import dispatch_observation_now
from db import (add_observation, list_observations, get_observation,
                list_history, list_executions)
from utils.coordinates import  sexagesimal_to_decimal

router = APIRouter(prefix="/api/v1")

class ObservationRequest(BaseModel):
    target_name: str
    ra: float
    dec: float
    frames: dict[str, int]    # {filtro: n_pose}, es. {"L": 30, "R": 30}; le chiavi sono i filtri
    exposition: float
    binning: str = "BIN1X1"   # BIN1X1 / BIN2X2 / BIN4X4 -> tradotto in modalita' camera
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

    @field_validator("binning")
    @classmethod
    def _valid_binning(cls, value):
        if value not in config.BINNING_TO_MODE:
            raise ValueError(f"binning non valido: {value}. Ammessi: {list(config.BINNING_TO_MODE)}")
        return value

    @field_validator("frames")
    @classmethod
    def _valid_frames(cls, value):
        if not value:
            raise ValueError("serve almeno un filtro con pose")
        for f, n in value.items():
            if f not in config.FILTER_LIST:
                raise ValueError(f"filtro non valido: {f}. Ammessi: {config.FILTER_LIST}")
            if n < 1:
                raise ValueError(f"le pose per il filtro {f} devono essere >= 1")
        return value

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
    obs = add_observation(request.model_dump())
    return {"status": "success", "id": obs.id,
            "message": f"Osservazione per {request.target_name} salvata (id {obs.id})."}

@router.get("/observations")
async def get_observations():
    return list_observations()

@router.get("/history")
async def get_history():
    """Storico delle osservazioni completate definitivamente, ciascuna col dettaglio
    delle notti in cui e' stata ripresa (dal diario di bordo)."""
    return [
        {**record.model_dump(),
         "nights": list_executions(observation_id=record.observation_id)}
        for record in list_history()
    ]

@router.get("/executions")
async def get_executions(night: str | None = None):
    """Il diario di bordo grezzo: cosa e' stato eseguito e com'e' andato (ok/alert/
    timeout), eventualmente di una sola notte. Comprende anche le osservazioni non
    ancora completate."""
    return list_executions(night=night)

@router.get("/config")
async def get_config():
    """Liste per popolare i menu del frontend (fonte unica: config.py)."""
    return {
        "binning": list(config.BINNING_TO_MODE.keys()),
        "filters": config.FILTER_LIST,
        "frame_types": config.FRAME_TYPE_LIST,
    }

@router.post("/dispatch/{observation_id}")
async def dispatch_now(observation_id: int):
    """Collaudo manuale: fa partire UNA osservazione ora (startup + preset inclusi)."""
    obs = get_observation(observation_id)
    if obs is None:
        return {"error": f"osservazione {observation_id} non trovata"}
    return await dispatch_observation_now(obs)