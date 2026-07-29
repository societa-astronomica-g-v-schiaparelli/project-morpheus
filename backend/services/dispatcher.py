import asyncio

import config
from services.script_generator import IndigoScriptGenerator
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

def _full_script(body):
    """Ogni invio a INDIGO e' un'UNICA esecuzione: preludio + script finalizzato.
    (INDIGO vuole il preludio insieme allo script, non 'seminato' a parte.)"""
    return run_script(PRELUDE + "\n" + generator.finalize_script(body))

async def send_observation(ws, obs):
    """Genera lo script per UNA osservazione (preludio incluso) e lo invia a INDIGO."""
    body = generator.generate_observation(
        target_name=obs.target_name, ra=obs.ra, dec=obs.dec,
        frames=obs.frames, exposition=obs.exposition, filters=obs.filters,
        mode=obs.mode, guide=obs.guide, focus=obs.focus, sequential=obs.sequential,
    )
    await ws.send(_full_script(body))

async def send_startup(ws):
    """Accende i sistemi a inizio nottata (es. raffreddamento camera)."""
    await ws.send(_full_script(generator.generate_startup()))

async def send_shutdown(ws):
    """Mette in sicurezza e spegne a fine nottata (park + cooler off)."""
    await ws.send(_full_script(generator.generate_shutdown()))

async def await_sequence(ws, max_seconds):
    """
    FEEDBACK: dopo aver avviato una sequenza, ascolta i messaggi di INDIGO finche'
    non termina. Ritorna (esito, progressi):
      - esito: 'ok' (SEQUENCE_STATE=Ok) | 'alert' (Alert) | 'timeout'
      - progressi: ultimo dizionario di item di SEQUENCE_STATE (STEP, PROGRESS, ...)
    Legge di continuo -> stato reale + buffer sempre svuotato.
    Assunzione: l'invio appena fatto porta SEQUENCE_STATE a Busy, quindi il primo
    stato terminale che vediamo appartiene a questa sequenza.
    """
    deadline = asyncio.get_event_loop().time() + max_seconds
    progress = {}
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=config.WS_RECV_TIMEOUT)
        except asyncio.TimeoutError:
            continue
        try:
            message = json.loads(raw)
        except Exception:
            continue
        for _, v in message.items():
            if not isinstance(v, dict) or v.get("name") != "SEQUENCE_STATE":
                continue
            state = v.get("state")
            progress = {i["name"]: i["value"] for i in v.get("items", [])}
            if state == "Ok":
                return "ok", progress
            if state == "Alert":
                return "alert", progress
    return "timeout", progress


async def dispatch_observation_now(obs, do_setup=True, wait_seconds=180):
    """
    Collaudo MANUALE on-demand: connette a INDIGO, (opzionale) accende/seleziona i
    dispositivi, invia UNA osservazione (preludio incluso) e aspetta l'esito.
    NON aggiorna il DB (e' una prova ripetibile). Ritorna {esito, progressi}.
    'do_setup=False' salta setup_devices: utile se i dispositivi li seleziona gia'
    un profilo INDIGO dei tecnici (evita il conflitto 'busy/in use').
    """
    try:
        async with websockets.connect(config.INDIGO_WS_URL, open_timeout=5, max_size=None) as ws:
            if do_setup:
                await setup_devices(ws)
            await send_observation(ws, obs)
            outcome, progress = await await_sequence(ws, wait_seconds)
            return {"outcome": outcome, "progress": progress}
    except Exception as e:
        return {"outcome": "connection_error", "detail": str(e)}


