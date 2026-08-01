from fastapi import APIRouter
from astropy.time import Time
import config
from db import observations_to_schedule, save_plan, list_slots, cancel_night
from services.scheduler import plan_campaign
from services.weather import weather_is_favorable

router = APIRouter(prefix="/api/v1")


def _iso(t):
    """Converte un Time di Astropy in stringa ISO (o None). E' il 'confine' dove
    passiamo dagli oggetti interni a un formato serializzabile in JSON."""
    return t.iso if t is not None else None


def _serialize_schedule(s):
    return {
        "night_start": _iso(s["night_start"]),
        "night_end": _iso(s["night_end"]),
        "scheduled": [
            {
                "observation_id": e.get("id"),
                "name": e["name"],
                "start": _iso(e["start"]),              # inizio slot = inizio preparazione
                "frames_start": _iso(e["frames_start"]),  # quando partono davvero le pose
                "end": _iso(e["end"]),
                "duration_minutes": round(e["duration_minutes"], 1),
                "frames": e.get("frames"),
                "fixed": e.get("fixed", False),
                "partial": e.get("partial", False),
            }
            for e in s["scheduled"]
        ],
        "unplaced": s["unplaced"],
        "conflicts": s["conflicts"],
    }


def _serialize_plan(plan):
    return {
        "by_night": [
            {"date": n["date"].iso[:10], "schedule": _serialize_schedule(n["schedule"])}
            for n in plan["by_night"]
        ],
        "free_unscheduled": [
            {"name": t["name"], "frames": t["frames"]} for t in plan["free_unscheduled"]
        ],
        "fixed_unschedulable": plan["fixed_unschedulable"],
    }


def _recompute_plan(date: str, nights: int, min_altitude: float):
    """Ripianifica dallo STATO ATTUALE del DB (osservazioni ancora da fare, con le
    pose gia' fatte gia' scontate) e salva il piano fresco. Ritorna il piano."""
    observations = observations_to_schedule()
    targets = [o.to_target() for o in observations]
    plan = plan_campaign(targets, Time(date), nights=nights, min_altitude=min_altitude)
    save_plan(plan)   # rende il piano persistente (tabella ScheduledSlot)
    return plan


@router.post("/schedule")
async def make_schedule(date: str, nights: int = config.NIGHTS_HORIZON,
                        min_altitude: float = config.DEFAULT_MIN_ALTITUDE):
    """
    Pianifica le osservazioni da fare sulla campagna di 'nights' notti a partire
    da 'date' (giorno della prima sera). Restituisce il piano in JSON.
    """
    return _serialize_plan(_recompute_plan(date, nights, min_altitude))


@router.get("/weather")
async def weather(date: str):
    """Verdetto meteo per la notte 'date' (giorno della serata osservativa, es. 2026-07-26)."""
    return weather_is_favorable(date)


@router.post("/night/start")
async def start_night(date: str, nights: int = config.NIGHTS_HORIZON,
                      min_altitude: float = config.DEFAULT_MIN_ALTITUDE):
    """
    Inizio nottata:
      - se il meteo e' avverso, ANNULLA il piano di stanotte (vince su tutto);
      - se e' favorevole, RIPIANIFICA dallo stato attuale (cosi' assorbe eventuali
        notti saltate o pose gia' fatte) e restituisce gli slot freschi da eseguire.
    """
    weather = weather_is_favorable(date)
    if not weather["favorable"]:
        cancelled = cancel_night(date)
        return {"night": date, "action": "annullata", "weather": weather,
                "slots_cancelled": cancelled}
    _recompute_plan(date, nights, min_altitude)   # piano sempre fresco prima di eseguire
    slots = list_slots(date)
    return {"night": date, "action": "via libera", "weather": weather,
            "slots": [{"start": s.start, "end": s.end, "target": s.target_name,
                       "frames": s.frames} for s in slots]}
