from services.script_generator import IndigoScriptGenerator
from pathlib import Path
import websockets, json

PRELUDE = (Path(__file__).parent.parent / "vendor" / "Sequencer.js").read_text()

observation_queue = []

def add_observation_to_queue(request_data: dict):
    observation_queue.append(request_data)

def run_script(script):
    return json.dumps({"newTextVector": {"device": "Scripting Agent",
        "name": "AGENT_SCRIPTING_RUN_SCRIPT", "items": [{"name": "SCRIPT", "value": script}]}})

async def generate_scripts():
    if not observation_queue:
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
    
            for request in observation_queue:
                body = generator.generate_observation(
                    target_name=request["target_name"],
                    ra=request["ra"],
                    dec=request["dec"],
                    frames=request["frames"],
                    exposition=request["exposition"],
                    filters=request["filters"],
                    mode=request["mode"],
                    guide=request["guide"],
                    focus=request["focus"],
                    sequential=request["sequential"]
                )
                print(f"Script generato per {request['target_name']}, lo invio a INDIGO...")

                script = generator.finalize_script(body)
                print(script)
            
                await websocket.send(script)
                print(f"Script per {request['target_name']} inviato con successo!")

    except Exception as e:
        print(f"Errore di connessione a {indigo_ws_url}: {e}")
        
    print("--- FINE PROCESSO ---")
