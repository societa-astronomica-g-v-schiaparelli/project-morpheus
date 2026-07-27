import asyncio

from services.script_generator import IndigoScriptGenerator
from db import list_observations
from pathlib import Path
import websockets, json

PRELUDE = (Path(__file__).parent.parent / "vendor" / "Sequencer.js").read_text()

DEVICES_TO_CONNECT = [
    "CCD Imager Simulator",
    "CCD Imager Simulator (wheel)",
    "CCD Imager Simulator (focuser)",
    "Mount Simulator",
]

AGENT_SELECTIONS = [
    ("Imager Agent", "FILTER_CCD_LIST",     "CCD Imager Simulator"),
    ("Imager Agent", "FILTER_WHEEL_LIST",   "CCD Imager Simulator (wheel)"),
    ("Imager Agent", "FILTER_FOCUSER_LIST", "CCD Imager Simulator (focuser)"),
    ("Mount Agent",  "FILTER_MOUNT_LIST",   "Mount Simulator"),
]

# indirizzo del server INDIGO e generatore di script (senza stato -> riusabile)
INDIGO_WS_URL = "ws://192.168.40.8:7624"
generator = IndigoScriptGenerator()

def run_script(script):
    return json.dumps({"newTextVector": {"device": "Scripting Agent",
        "name": "AGENT_SCRIPTING_RUN_SCRIPT", "items": [{"name": "SCRIPT", "value": script}]}})

def set_switch(device, property_name, item):
    return json.dumps({"newSwitchVector": {"device": device, "name": property_name,
                     "items": [{"name": item, "value": True}]}})

async def setup_devices(ws):
    for device in DEVICES_TO_CONNECT:
        print(f"Connessione a {device} in corso...")
        await ws.send(set_switch(device, "CONNECTION", "CONNECTED"))
        print(f"{device} connesso!")

    await asyncio.sleep(2)

    for agent, property_name, item in AGENT_SELECTIONS:
        print(f"Selezione {item} per {agent} in corso...")
        await ws.send(set_switch(agent, property_name, item))
        print(f"{item} selezionato per {agent}!")

# --- PRIMITIVE: le "mani" che parlano a INDIGO su una connessione 'ws' ---

async def inject_prelude(ws):
    """Semina la classe Sequence nell'agent. Va fatto UNA volta per connessione."""
    await ws.send(run_script(PRELUDE))

async def send_observation(ws, obs):
    """Genera lo script per UNA osservazione e lo invia a INDIGO."""
    body = generator.generate_observation(
        target_name=obs.target_name, ra=obs.ra, dec=obs.dec,
        frames=obs.frames, exposition=obs.exposition, filters=obs.filters,
        mode=obs.mode, guide=obs.guide, focus=obs.focus, sequential=obs.sequential,
    )
    script = generator.finalize_script(body)
    await ws.send(run_script(script))

async def send_startup(ws):
    """Accende i sistemi a inizio nottata (es. raffreddamento camera)."""
    await ws.send(run_script(generator.finalize_script(generator.generate_startup())))

async def send_shutdown(ws):
    """Mette in sicurezza e spegne a fine nottata (park + cooler off)."""
    await ws.send(run_script(generator.finalize_script(generator.generate_shutdown())))


async def generate_scripts():
    """
    Percorso MANUALE/di prova (endpoint /generate): connette, accende i dispositivi,
    semina il preludio e invia subito tutte le osservazioni 'pending'.
    Nella versione finale sara' morpheus.py a orchestrare tutto agli orari giusti,
    slot per slot; questo resta utile per collaudi rapidi.
    """
    observations = list_observations(status="pending")
    if not observations:
        print("Nessuna osservazione in coda.")
        return

    print("--- INIZIO GENERAZIONE ED INVIO SCRIPT ---")
    try:
        async with websockets.connect(INDIGO_WS_URL, open_timeout=3) as ws:
            print(f"Connesso a {INDIGO_WS_URL}")
            await setup_devices(ws)      # accende/associa i dispositivi
            await inject_prelude(ws)     # semina la classe Sequence
            for obs in observations:
                print(f"Invio script per {obs.target_name}...")
                await send_observation(ws, obs)
    except Exception as e:
        print(f"Errore di connessione a {INDIGO_WS_URL}: {e}")
    print("--- FINE PROCESSO ---")
