"""
Esecuzione: progresso, archiviazione nello storico, diario di bordo e stato live.

Tutto gira su un database usa-e-getta (fixture 'temp_db'): quello vero non viene
mai toccato. Il WebSocket verso INDIGO e' finto, cosi' i test non hanno bisogno
di un telescopio (ne' di un simulatore) acceso.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta

import pytest
from sqlmodel import Session

import config
from services import dispatcher


def observation(db, **extra):
    return db.add_observation({"target_name": "M31", "ra": 0.712, "dec": 41.27,
                               "frames": {"L": 2, "R": 1}, "exposition": 60, **extra})


def slot(db, obs, night, frames, start="22:00:00", end="23:00:00"):
    riga = db.ScheduledSlot(observation_id=obs.id, target_name=obs.target_name,
                            night=night, start=f"{night} {start}", end=f"{night} {end}",
                            frames=frames)
    with Session(db.engine) as session:
        session.add(riga)
        session.commit()
        session.refresh(riga)
        return riga


# ============================================================ progresso e archiviazione

def test_partial_progress_keeps_the_request_open(temp_db):
    """Finche' mancano pose, la richiesta resta fra quelle da fare."""
    obs = observation(temp_db)
    esito = temp_db.record_progress(obs.id, {"L": 2})
    assert esito.status == "in_progress"
    assert temp_db.get_observation(obs.id) is not None


def test_completed_observation_moves_to_the_history(temp_db):
    """Completata al 100%: esce dalle richieste ed entra nello storico definitivo."""
    obs = observation(temp_db)
    temp_db.record_progress(obs.id, {"L": 2})
    archiviata = temp_db.record_progress(obs.id, {"R": 1})

    assert temp_db.get_observation(obs.id) is None
    assert [o.id for o in temp_db.list_observations()] == []
    assert archiviata.target_name == "M31"
    assert archiviata.frames == {"L": 2, "R": 1}
    assert archiviata.completed_at is not None


def test_archiving_removes_its_slots_but_not_the_others(temp_db):
    """Il piano non deve puntare a una richiesta che non esiste piu'."""
    finita = observation(temp_db)
    altra = observation(temp_db, target_name="Vega")
    slot(temp_db, finita, "2026-08-01", {"L": 2})
    slot(temp_db, altra, "2026-08-01", {"L": 2})

    temp_db.record_progress(finita.id, {"L": 2, "R": 1})

    rimasti = temp_db.list_slots()
    assert all(s.observation_id != finita.id for s in rimasti)
    assert any(s.observation_id == altra.id for s in rimasti)


def test_archiving_twice_is_harmless(temp_db):
    """Riprovare ad archiviare qualcosa di gia' archiviato non deve rompere nulla."""
    obs = observation(temp_db)
    temp_db.record_progress(obs.id, {"L": 2, "R": 1})
    assert temp_db.archive_observation(obs.id) is None


# ============================================================ diario di bordo

def test_execution_log_survives_the_archiving(temp_db):
    """Il dettaglio per notte resta anche quando la richiesta sparisce."""
    obs = observation(temp_db)
    temp_db.log_execution(slot(temp_db, obs, "2026-08-01", {"L": 2}), "ok")
    temp_db.log_execution(slot(temp_db, obs, "2026-08-03", {"R": 1}), "ok")
    temp_db.record_progress(obs.id, {"L": 2, "R": 1})

    diario = temp_db.list_executions(observation_id=obs.id)
    assert [e.night for e in diario] == ["2026-08-01", "2026-08-03"]


def test_failed_sequences_are_logged_too(temp_db):
    """Anche cio' che va male finisce nel diario: e' li' che serve di piu'."""
    obs = observation(temp_db)
    temp_db.log_execution(slot(temp_db, obs, "2026-08-01", {"L": 2}), "alert")
    assert [e.outcome for e in temp_db.list_executions()] == ["alert"]
    assert temp_db.get_observation(obs.id).frames_done == {}


def test_execution_log_can_be_filtered_by_night(temp_db):
    obs = observation(temp_db)
    temp_db.log_execution(slot(temp_db, obs, "2026-08-01", {"L": 1}), "ok")
    temp_db.log_execution(slot(temp_db, obs, "2026-08-02", {"L": 1}), "ok")
    assert len(temp_db.list_executions(night="2026-08-02")) == 1


# ============================================================ stato live

def test_live_status_round_trip(temp_db):
    temp_db.set_live_status(phase="osservazione", message="Ripresa di M31",
                            target_name="M31", progress=12, progress_total=30)
    stato = temp_db.get_live_status()
    assert stato.phase == "osservazione" and stato.target_name == "M31"
    assert stato.progress == 12


def test_live_status_clears_the_fields_not_passed(temp_db):
    """Lo stato descrive l'istante presente: non deve restare appeso il target di prima."""
    temp_db.set_live_status(phase="osservazione", message="x", target_name="M31", progress=5)
    temp_db.set_live_status(phase="spegnimento", message="y")
    stato = temp_db.get_live_status()
    assert stato.target_name is None and stato.progress is None


def test_live_status_stays_a_single_row(temp_db):
    for i in range(3):
        temp_db.set_live_status(phase="attesa", message=str(i))
    assert temp_db.get_live_status().id == 1


def test_snapshot_survives_an_empty_status(temp_db):
    """Se morpheus non ha mai scritto, l'app non deve rompersi."""
    from api import live_router
    istantanea = live_router._snapshot()
    assert istantanea["active"] is False and "mai" in istantanea["message"]


def test_morpheus_is_considered_inactive_after_a_long_silence(temp_db):
    """Non potendo dire 'sto chiudendo', lo si deduce dal silenzio."""
    from api import live_router
    temp_db.set_live_status(phase="osservazione", message="Ripresa in corso")
    assert live_router._snapshot()["active"] is True

    with Session(temp_db.engine) as session:
        riga = session.get(temp_db.LiveStatus, 1)
        riga.updated_at = (datetime.now(timezone.utc)
                           - timedelta(seconds=config.LIVE_STALE_AFTER + 60))
        session.add(riga)
        session.commit()

    istantanea = live_router._snapshot()
    assert istantanea["active"] is False and "non risponde" in istantanea["message"]


# ============================================================ feedback da INDIGO

def sequence_message(state, progress, total):
    """Un messaggio SEQUENCE_STATE come lo manderebbe INDIGO."""
    return json.dumps({"setNumberVector": {
        "name": "SEQUENCE_STATE", "state": state,
        "items": [{"name": "PROGRESS", "value": progress},
                  {"name": "PROGRESS_TOTAL", "value": total},
                  {"name": "EXPOSURE", "value": 5}, {"name": "EXPOSURE_TOTAL", "value": 60}]}})


class FakeWebSocket:
    """Restituisce i messaggi preparati, poi resta in silenzio (fa scattare il timeout)."""
    def __init__(self, messages):
        self.messages = list(messages)

    async def recv(self):
        if not self.messages:
            await asyncio.sleep(3600)
        return self.messages.pop(0)


def test_progress_callback_is_called_on_every_update():
    """Il feedback e' vivo: ogni aggiornamento passa, non solo l'ultimo."""
    visti = []
    ws = FakeWebSocket([sequence_message("Busy", 1, 3), sequence_message("Busy", 2, 3),
                        json.dumps({"message": "rumore da ignorare"}),
                        sequence_message("Ok", 3, 3)])
    esito, ultimo = asyncio.run(dispatcher.await_sequence(ws, 10, on_progress=visti.append))

    assert esito == "ok"
    assert [v["PROGRESS"] for v in visti] == [1, 2, 3]
    assert ultimo["PROGRESS"] == 3


def test_a_broken_callback_does_not_fail_the_sequence():
    """Un problema nel mostrare lo stato non deve far fallire un'osservazione."""
    def rotta(_):
        raise RuntimeError("boom")

    ws = FakeWebSocket([sequence_message("Busy", 1, 2), sequence_message("Ok", 2, 2)])
    assert asyncio.run(dispatcher.await_sequence(ws, 10, on_progress=rotta))[0] == "ok"


def test_alert_state_is_reported():
    ws = FakeWebSocket([sequence_message("Busy", 1, 3), sequence_message("Alert", 1, 3)])
    assert asyncio.run(dispatcher.await_sequence(ws, 10))[0] == "alert"


def test_stale_ok_before_busy_is_ignored():
    """Lo stato iniziale della sequenza e' gia' 'Ok': non deve chiudere l'attesa prima
    che la sequenza sia davvero partita. Si aspetta il 'Busy', poi il vero 'Ok'."""
    ws = FakeWebSocket([sequence_message("Ok", 0, 1),     # Ok residuo/iniziale, da ignorare
                        sequence_message("Busy", 0, 1),   # la sequenza parte davvero
                        sequence_message("Ok", 1, 1)])     # vero completamento
    esito, ultimo = asyncio.run(dispatcher.await_sequence(ws, 10))
    assert esito == "ok" and ultimo["PROGRESS"] == 1


def test_silence_becomes_a_timeout():
    ws = FakeWebSocket([])
    assert asyncio.run(dispatcher.await_sequence(ws, 0.2))[0] == "timeout"


# ============================================================ il flusso SSE

def test_sse_stream_emits_the_current_state(temp_db):
    """Il flusso SSE manda subito lo stato corrente, senza aspettare un cambiamento.
    (Keep-alive, disconnessione e chiusura li gestisce l'SSE nativo di FastAPI.)"""
    from api import live_router
    temp_db.set_live_status(phase="attesa", message="In attesa della notte")

    async def primo_evento():
        async for evento in live_router.live_stream():
            return evento

    evento = asyncio.run(primo_evento())
    assert evento.data["phase"] == "attesa"
    assert evento.data["active"] is True
