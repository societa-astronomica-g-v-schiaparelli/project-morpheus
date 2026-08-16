import asyncio

import config
from services.script_generator import IndigoScriptGenerator
from pathlib import Path
import websockets, json

PRELUDE = (Path(__file__).parent.parent / "vendor" / "Sequencer.js").read_text()

generator = IndigoScriptGenerator()

def run_script(script):
    return json.dumps({"newTextVector": {"device": "Scripting Agent",
        "name": "AGENT_SCRIPTING_RUN_SCRIPT", "items": [{"name": "SCRIPT", "value": script}]}})


async def send_prelude(ws):
    """Carica UNA sola volta a serata il preludio (Sequencer.js, ~1600 righe): e' la
    DEFINIZIONE della classe Sequence, non un oggetto. Va mandato all'apertura della
    connessione, PRIMA di qualsiasi altro script. Cosi' i comandi successivi possono
    usare 'new Sequence()' senza riportarsi dietro il preludione, e ogni invio resta
    piccolo (~60 KB il preludio una volta, pochi byte i singoli script).
    E' il modo in cui lavora l'Ain; il telescopio vero (babele) sembra rifiutare i
    messaggi troppo grandi, ed e' per questo che prima glielo incollavamo dentro ogni
    volta e non partiva nulla. Poi ASPETTA che il preludio sia interpretato, altrimenti
    il primo script arriverebbe prima che la classe Sequence esista."""
    await ws.send(run_script(PRELUDE))
    await asyncio.sleep(config.PRELUDE_SETTLE)


def _sequence_message(body):
    """Confeziona il messaggio INDIGO che esegue UNA sequenza: 'new Sequence()' +
    corpo + 'start()' (via finalize_script), incapsulato in un RUN_SCRIPT. NIENTE
    preludio: quello si manda una volta sola, all'inizio, con send_prelude()."""
    return run_script(generator.finalize_script(body))

async def send_observation(ws, obs, wait_until=None):
    """Genera lo script per UNA osservazione e lo invia a INDIGO (il preludio e' gia'
    stato caricato all'apertura della connessione, vedi send_prelude).
    'wait_until' (ISO UTC), se passato, inchioda l'inizio delle pose a quell'istante:
    lo usano gli orari fissi perche' le pose partano all'ora esatta chiesta dall'utente."""
    body = generator.generate_observation(
        target_name=obs.target_name, ra=obs.ra, dec=obs.dec,
        frames=obs.frames, exposition=obs.exposition,
        binning=obs.binning, guide=obs.guide, focus=obs.focus, sequential=obs.sequential,
        wait_until=wait_until,
    )
    await ws.send(_sequence_message(body))

async def send_startup(ws):
    """Accende i sistemi a inizio nottata: carica il preset hardware e avvia il
    raffreddamento della camera.
    Poi ASPETTA: load_config impiega qualche secondo e se nel frattempo arriva un
    altro script INDIGO lo rifiuta (SEQUENCE_STATE -> Alert). La pausa sta qui dentro
    e non nel chiamante, cosi' nessuno puo' dimenticarsela."""
    await ws.send(_sequence_message(generator.generate_startup()))
    await asyncio.sleep(config.STARTUP_SETTLE)

async def send_shutdown(ws):
    """Mette in sicurezza e spegne a fine nottata (park + cooler off)."""
    await ws.send(_sequence_message(generator.generate_shutdown()))

async def await_sequence(ws, max_seconds, on_progress=None):
    """
    FEEDBACK: dopo aver avviato una sequenza, ascolta i messaggi di INDIGO finche'
    non termina. Ritorna (esito, progressi):
      - esito: 'ok' (SEQUENCE_STATE=Ok) | 'alert' (Alert) | 'timeout'
      - progressi: ultimo dizionario di item di SEQUENCE_STATE (STEP, PROGRESS, ...)
    Legge di continuo -> stato reale + buffer sempre svuotato.
    Assunzione: l'invio appena fatto porta SEQUENCE_STATE a Busy, quindi il primo
    stato terminale che vediamo appartiene a questa sequenza.

    'on_progress', se passato, viene chiamato a OGNI aggiornamento di SEQUENCE_STATE
    con il dizionario di quel momento: e' cosi' che il feedback diventa "vivo" invece
    di arrivare solo alla fine. Se solleva un'eccezione la sequenza NON si ferma:
    un problema nel mostrare lo stato non deve far fallire un'osservazione.
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
            if on_progress is not None:
                try:
                    on_progress(progress)
                except Exception as e:
                    print(f"[dispatcher] avviso: on_progress ha fallito ({e})")
            if state == "Ok":
                return "ok", progress
            if state == "Alert":
                return "alert", progress
    return "timeout", progress


async def dispatch_observation_now(obs, wait_seconds=180):
    """
    Collaudo MANUALE on-demand: connette a INDIGO, esegue lo startup (carica il preset
    hardware + accende), invia UNA osservazione (preludio incluso) e aspetta l'esito.
    NON aggiorna il DB (e' una prova ripetibile). Ritorna {outcome, progress}.
    """
    try:
        async with websockets.connect(config.INDIGO_WS_URL, open_timeout=5, max_size=None,
                                      ping_interval=config.WS_PING_INTERVAL) as ws:
            await send_prelude(ws)   # una volta all'apertura: definisce la classe Sequence
            await send_startup(ws)
            await send_observation(ws, obs)
            outcome, progress = await await_sequence(ws, wait_seconds)
            return {"outcome": outcome, "progress": progress}
    except Exception as e:
        return {"outcome": "connection_error", "detail": str(e)}


