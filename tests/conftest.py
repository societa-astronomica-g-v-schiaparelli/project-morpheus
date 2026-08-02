"""
Impostazioni comuni a tutti i test.

La radice degli import del progetto e' 'backend/' (si scrive 'import config', non
'import backend.config'), quindi la aggiungiamo al percorso di ricerca dei moduli.
Qui vivono anche gli "attrezzi" riusabili dai test: un database usa-e-getta e
l'interruttore della modalita' simulazione.
"""
import sys
from pathlib import Path

import pytest
from astropy.time import Time
from sqlmodel import create_engine

BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def modalita_pulite(monkeypatch):
    """Ogni test parte SEMPRE con gli interruttori di prova spenti, qualunque cosa ci
    sia in config.py. Senza questo i risultati cambiano a seconda di come hai lasciato
    TEST_MODE dopo una serata in osservatorio, e i test non valgono piu' niente.
    Chi ha bisogno di accenderli lo fa da se' (fixture 'simulation' o monkeypatch)."""
    import config

    monkeypatch.setattr(config, "TEST_MODE", False)
    monkeypatch.setattr(config, "SIMULATION_MODE", False)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Un database vuoto e usa-e-getta per ogni test: quello vero non viene mai toccato."""
    import db as db_module

    monkeypatch.setattr(db_module, "engine",
                        create_engine(f"sqlite:///{tmp_path / 'prova.db'}"))
    db_module.init_db()
    return db_module


@pytest.fixture
def simulation(monkeypatch):
    """Accende la modalita' simulazione con un istante zero FISSO, cosi' i test sono
    ripetibili. All'uscita rimette tutto com'era."""
    import config
    from services import astronomy

    epoch = Time("2026-08-01 12:00:00")
    monkeypatch.setattr(config, "SIMULATION_MODE", True)
    astronomy.reset_simulation_clock(epoch)
    yield epoch
    astronomy.reset_simulation_clock(None)
