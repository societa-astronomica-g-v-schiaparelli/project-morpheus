from services.script_generator import IndigoScriptGenerator
from db import list_observations
from pathlib import Path
import websockets, json

PRELUDE = (Path(__file__).parent.parent / "vendor" / "Sequencer.js").read_text()

def run_script(script):
    return json.dumps({"newTextVector": {"device": "Scripting Agent",
        "name": "AGENT_SCRIPTING_RUN_SCRIPT", "items": [{"name": "SCRIPT", "value": script}]}})

async def generate_scripts():
    observations = list_observations(status="pending")
    if not observations:
        print("Nessuna osservazione in coda.")
        return

    generator = IndigoScriptGenerator()

    # INDIGO WebSocket URL (Da sostituire con l'IP esatto di Babele -> http://morpheus.astrogeo.va.it:7624 -> 192.168.40.8:7624)
    indigo_ws_url = "ws://192.168.40.8:7624"

    print("--- INIZIO GENERAZIONE ED INVIO SCRIPT ---")

    try:
        async with websockets.connect(indigo_ws_url, open_timeout=3) as websocket:
            print("Connesso a " + indigo_ws_url + "  invio del preludio in corso...")
            await websocket.send(run_script(PRELUDE))

            for obs in observations:
                body = generator.generate_observation(
                    target_name=obs.target_name,
                    ra=obs.ra,
                    dec=obs.dec,
                    frames=obs.frames,
                    exposition=obs.exposition,
                    filters=obs.filters,
                    mode=obs.mode,
                    guide=obs.guide,
                    focus=obs.focus,
                    sequential=obs.sequential
                )
                print(f"Script generato per {obs.target_name}, lo invio a INDIGO...")

                script = generator.finalize_script(body)
                print(script)

                await websocket.send(script)
                print(f"Script per {obs.target_name} inviato con successo!")

    except Exception as e:
        print(f"Errore di connessione a {indigo_ws_url}: {e}")

    print("--- FINE PROCESSO ---")
