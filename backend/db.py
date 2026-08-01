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
    frames: dict[str, int] = Field(sa_column=Column(JSON))  # {filtro: n_pose}, es. {"L":30,"R":30}
    exposition: float
    binning: str = "BIN1X1"                                  # tradotto in modalita' camera (BINNING_TO_MODE)
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
    frames_done: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON))  # {filtro: pose fatte}
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # per l'ordine FIFO
    )

    def remaining_frames(self) -> dict[str, int]:
        """Pose che RESTANO da fare, per filtro (frames - frames_done, solo se > 0)."""
        return {f: n - self.frames_done.get(f, 0)
                for f, n in self.frames.items() if n - self.frames_done.get(f, 0) > 0}

    def to_target(self):
        """Traduce l'osservazione nel dict che si aspetta lo scheduler (target_name -> name),
        includendo solo i campi di scheduling effettivamente impostati. Allo scheduler passa
        quante pose RESTANO per filtro."""
        target = {"id": self.id, "name": self.target_name, "ra": self.ra, "dec": self.dec,
                  "frames": self.remaining_frames(), "exposition": self.exposition}
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
    frames: dict[str, int] = Field(sa_column=Column(JSON))  # {filtro: n_pose} di questo slot
    partial: bool = False
    fixed: bool = False


class ExecutionRecord(SQLModel, table=True):
    """
    Il DIARIO DI BORDO: una riga per ogni slot davvero eseguito, scritta da morpheus
    appena la sequenza finisce, qualunque sia l'esito. E' il dettaglio per notte dello
    storico ("M31 ripresa il 3, il 5 e il 7 agosto"). Tabella in sola aggiunta: nessuna
    altra logica la legge e save_plan non la tocca, quindi sopravvive alle ripianificazioni.
    NB niente foreign_key su observation_id: la richiesta viene archiviata (e cancellata)
    quando si completa, ma il suo diario deve restare.
    """
    id: int | None = Field(default=None, primary_key=True)
    observation_id: int | None = None
    target_name: str
    night: str            # giorno della sera, es. "2026-08-01"
    start: str            # orario pianificato dello slot (ISO UTC)
    end: str
    frames: dict[str, int] = Field(sa_column=Column(JSON))   # pose previste dallo slot
    outcome: str          # ok | alert | timeout (esito di await_sequence)
    logged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObservationHistory(SQLModel, table=True):
    """
    Lo STORICO DEFINITIVO: una richiesta che e' stata completata al 100% esce dalla
    tabella Observation e finisce qui. Cosi' la tabella delle richieste contiene solo
    cio' che resta da fare, e lo storico non viene piu' toccato da nessuno.
    Il dettaglio delle singole notti sta in ExecutionRecord, legato da 'observation_id'.
    """
    id: int | None = Field(default=None, primary_key=True)
    observation_id: int          # l'id che aveva in Observation (lega gli ExecutionRecord)

    target_name: str
    ra: float
    dec: float
    frames: dict[str, int] = Field(sa_column=Column(JSON))   # pose richieste (tutte fatte)
    exposition: float
    binning: str
    guide: bool
    focus: bool
    sequential: bool

    fixed_start: str | None = None
    min_altitude: float | None = None
    moon_check: bool = False
    moon_base_angle: float = 90
    splittable: bool = False

    created_at: datetime         # quando era stata richiesta
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


def get_observation(observation_id: int) -> Observation | None:
    """Recupera una singola osservazione dal suo id (serve a morpheus per generare
    lo script partendo da uno slot del piano)."""
    with Session(engine) as session:
        return session.get(Observation, observation_id)


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
                    frames=e.get("frames") or {},
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


def log_execution(slot: ScheduledSlot, outcome: str) -> ExecutionRecord:
    """Scrive nel diario di bordo com'e' andato UNO slot eseguito (ok/alert/timeout).
    Da chiamare sempre, anche quando va male: e' proprio li' che lo storico serve."""
    record = ExecutionRecord(
        observation_id=slot.observation_id, target_name=slot.target_name,
        night=slot.night, start=slot.start, end=slot.end,
        frames=slot.frames, outcome=outcome,
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def archive_observation(observation_id: int) -> ObservationHistory | None:
    """Sposta un'osservazione COMPLETATA dalle richieste allo storico definitivo:
    copia i suoi dati in ObservationHistory, toglie dal piano gli slot che la
    riguardano (non deve restare un piano che punta a una richiesta inesistente) e
    infine cancella la richiesta. Il diario di bordo (ExecutionRecord) resta.
    Ritorna la riga di storico, o None se l'osservazione non c'e' (gia' archiviata)."""
    with Session(engine) as session:
        obs = session.get(Observation, observation_id)
        if obs is None:
            return None
        record = ObservationHistory(
            observation_id=obs.id, target_name=obs.target_name, ra=obs.ra, dec=obs.dec,
            frames=obs.frames, exposition=obs.exposition, binning=obs.binning,
            guide=obs.guide, focus=obs.focus, sequential=obs.sequential,
            fixed_start=obs.fixed_start, min_altitude=obs.min_altitude,
            moon_check=obs.moon_check, moon_base_angle=obs.moon_base_angle,
            splittable=obs.splittable, created_at=obs.created_at,
        )
        session.add(record)
        for slot in session.exec(select(ScheduledSlot)
                                 .where(ScheduledSlot.observation_id == observation_id)):
            session.delete(slot)
        session.delete(obs)
        session.commit()
        session.refresh(record)
        return record


def list_history() -> list[ObservationHistory]:
    """Lo storico delle osservazioni completate, dalla piu' recente."""
    with Session(engine) as session:
        return list(session.exec(select(ObservationHistory)
                                 .order_by(ObservationHistory.completed_at.desc())))


def list_executions(observation_id: int | None = None,
                    night: str | None = None) -> list[ExecutionRecord]:
    """Il diario di bordo, filtrabile per osservazione o per notte. In ordine di orario."""
    with Session(engine) as session:
        query = select(ExecutionRecord).order_by(ExecutionRecord.start)
        if observation_id is not None:
            query = query.where(ExecutionRecord.observation_id == observation_id)
        if night is not None:
            query = query.where(ExecutionRecord.night == night)
        return list(session.exec(query))


def record_progress(observation_id: int, frames_done: dict[str, int]):
    """Registra le pose scattate (dict {filtro: n}) SOMMANDOLE a quelle gia' fatte, e
    aggiorna lo stato: 'in_progress' se manca ancora qualcosa. Se invece non resta piu'
    nulla da fare, l'osservazione e' finita: viene ARCHIVIATA (esce da Observation e
    passa in ObservationHistory).
    Ritorna l'Observation aggiornata, oppure la riga di ObservationHistory se ha finito."""
    with Session(engine) as session:
        obs = session.get(Observation, observation_id)
        done = dict(obs.frames_done)   # nuova copia: i JSON vanno riassegnati, non mutati in place
        for f, n in frames_done.items():
            done[f] = done.get(f, 0) + n
        obs.frames_done = done
        completed = not obs.remaining_frames()
        obs.status = "completed" if completed else "in_progress"
        session.add(obs)
        session.commit()
        if not completed:
            session.refresh(obs)
            return obs
    return archive_observation(observation_id)
