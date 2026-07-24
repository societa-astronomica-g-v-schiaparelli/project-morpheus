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
