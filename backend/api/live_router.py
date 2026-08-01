"""
Feedback in tempo reale su cosa sta facendo morpheus.

morpheus e' un processo SEPARATO dall'app web: non possono chiamarsi a vicenda, ma
condividono il database. Lui scrive la sua riga di stato (LiveStatus), qui la si
rilegge e la si spinge al browser via SSE (Server-Sent Events), che e' un flusso HTTP
tenuto aperto su cui il server manda eventi quando ha qualcosa da dire.
"""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder

import config
from db import get_live_status

router = APIRouter(prefix="/api/v1")


def _snapshot():
    """Lo stato corrente in forma serializzabile, piu' 'active': morpheus e' vivo?
    Se la sua riga non viene aggiornata da un pezzo vuol dire che non sta girando
    (non ha modo di dirci "sto chiudendo", quindi lo si deduce dal silenzio)."""
    status = get_live_status()
    if status is None:
        return {"phase": "fermo", "message": "morpheus non e' mai stato avviato",
                "active": False}
    data = jsonable_encoder(status)
    silence = (datetime.now(timezone.utc) - status.updated_at.replace(tzinfo=timezone.utc)).total_seconds()
    data["active"] = silence < config.LIVE_STALE_AFTER
    if not data["active"]:
        data["message"] = f"morpheus non risponde da {int(silence // 60)} min"
    return data


@router.get("/live")
async def live():
    """Lo stato corrente, una volta sola (comodo per un controllo al volo o da curl)."""
    return _snapshot()


@router.get("/live/stream")
async def live_stream():
    """
    Lo stesso stato, ma in flusso continuo: la connessione resta aperta e ogni volta
    che lo stato CAMBIA parte un evento verso il browser. Quando non cambia nulla si
    manda un commento SSE (una riga che inizia con ':'), che il browser ignora ma
    tiene viva la connessione attraverso eventuali proxy.
    """
    async def events():
        last = None
        while True:
            data = _snapshot()
            if data != last:
                last = data
                yield f"data: {json.dumps(data)}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(config.LIVE_POLL_INTERVAL)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
