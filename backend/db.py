from pathlib import Path
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON

DATABASE_FILE = Path(__file__).parent / "morpheus.db"
engine = create_engine(f"sqlite:///{DATABASE_FILE}")

class Observation(SQLModel, table=True):
    """
    Una richiesta di osservazione, come vive nel database.
    Somiglia a ObservationRequest (l'input API) ma in piu' porta lo STATO che deve
    sopravvivere tra una notte e l'altra: quante pose sono gia' state fatte, se e'
    completata, e quando e' stata inviata (per il FIFO).
    """
    id: int | None = Field(default=None, primary_key=True)

    # --- dati della richiesta ---
    target_name: str
    ra: float
    dec: float
    frames: int
    exposition: float
    filters: list[str] = Field(sa_column=Column(JSON))  # lista -> salvata come JSON
    mode: str
    guide: bool = True
    focus: bool = True
    sequential: bool = False

    # --- parametri di scheduling ---
    fixed_start: str | None = None
    min_altitude: float | None = None
    moon_check: bool = False
    moon_base_angle: float = 90
    splittable: bool = False

    # --- stato che deve PERSISTERE ---
    status: str = "pending"          # pending | in_progress | completed | cancelled
    frames_done: int = 0             # pose gia' scattate (per split/rollover multi-notte)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # per l'ordine FIFO
    )

    def to_target(self):
        """Traduce l'osservazione nel dict che si aspetta lo scheduler (target_name -> name),
        includendo solo i campi di scheduling effettivamente impostati. Considera le pose
        gia' fatte: allo scheduler passa quante ne RESTANO (frames - frames_done)."""
        target = {"id": self.id, "name": self.target_name, "ra": self.ra, "dec": self.dec,
                  "frames": self.frames - self.frames_done, "exposition": self.exposition}
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


class ScheduledSlot(SQLModel, table=True):
    """
    Uno slot del piano: un'osservazione (o un suo pezzo, per lo split) piazzata in
    una notte a un orario preciso. E' l'output dello scheduler reso persistente, così
    il dispatcher può leggerlo e il frontend può mostrarlo.
    """
    id: int | None = Field(default=None, primary_key=True)
    observation_id: int | None = Field(default=None, foreign_key="observation.id")
    target_name: str
    night: str            # giorno della sera, es. "2026-07-19"
    start: str            # ISO UTC
    end: str
    frames: int
    partial: bool = False
    fixed: bool = False


def init_db():
    """Crea il file e le tabelle se non esistono ancora. Da chiamare all'avvio dell'app."""
    SQLModel.metadata.create_all(engine)


def add_observation(data: dict) -> Observation:
    """Salva una nuova osservazione (dict, es. da ObservationRequest.model_dump())."""
    obs = Observation(**data)
    with Session(engine) as session:
        session.add(obs)
        session.commit()
        session.refresh(obs)  # rilegge l'id assegnato dal database
        return obs


def list_observations(status: str | None = None) -> list[Observation]:
    """Elenca le osservazioni, eventualmente filtrando per stato. Ordinate per arrivo (FIFO)."""
    with Session(engine) as session:
        query = select(Observation).order_by(Observation.created_at)
        if status is not None:
            query = query.where(Observation.status == status)
        return list(session.exec(query))


def observations_to_schedule() -> list[Observation]:
    """Le osservazioni ancora da fare = da schedulare: 'pending' o 'in_progress'
    (quelle 'completed'/'cancelled' sono fuori). Ordinate per arrivo (FIFO)."""
    with Session(engine) as session:
        query = (select(Observation)
                 .where(Observation.status.in_(["pending", "in_progress"]))
                 .order_by(Observation.created_at))
        return list(session.exec(query))


def save_plan(plan: dict):
    """Salva il piano calcolato dallo scheduler nella tabella ScheduledSlot,
    sostituendo il piano precedente. Converte i Time in stringhe ISO."""
    with Session(engine) as session:
        for old in session.exec(select(ScheduledSlot)):   # via il piano vecchio
            session.delete(old)
        for night in plan["by_night"]:
            night_str = night["date"].iso[:10]
            for e in night["schedule"]["scheduled"]:
                session.add(ScheduledSlot(
                    observation_id=e.get("id"),
                    target_name=e["name"],
                    night=night_str,
                    start=e["start"].iso,
                    end=e["end"].iso,
                    frames=e.get("frames") or 0,
                    partial=e.get("partial", False),
                    fixed=e.get("fixed", False),
                ))
        session.commit()


def list_slots(night: str | None = None) -> list[ScheduledSlot]:
    """Legge gli slot del piano, eventualmente di una sola notte. Ordinati per orario."""
    with Session(engine) as session:
        query = select(ScheduledSlot).order_by(ScheduledSlot.start)
        if night is not None:
            query = query.where(ScheduledSlot.night == night)
        return list(session.exec(query))


def cancel_night(night: str) -> int:
    """Annulla il piano di una notte: cancella i suoi slot dal calendario (ScheduledSlot).
    Usato quando il meteo e' avverso. Ritorna quanti slot ha tolto. NON tocca le
    osservazioni (le richieste di osservazione restano, verranno ripianificate)."""
    with Session(engine) as session:
        slots = list(session.exec(select(ScheduledSlot).where(ScheduledSlot.night == night)))
        for s in slots:
            session.delete(s)
        session.commit()
        return len(slots)


def record_progress(observation_id: int, frames_done: int):
    """Registra 'frames_done' pose scattate per un'osservazione (le SOMMA a quelle gia'
    fatte) e aggiorna lo stato: 'completed' se ha finito, altrimenti 'in_progress'.
    Mantiene lo stato che serve al multi-notte."""
    with Session(engine) as session:
        obs = session.get(Observation, observation_id)
        obs.frames_done += frames_done
        obs.status = "completed" if obs.frames_done >= obs.frames else "in_progress"
        session.add(obs)
        session.commit()
        session.refresh(obs)
        return obs
