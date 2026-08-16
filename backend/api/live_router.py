"""
Feedback in tempo reale su cosa sta facendo morpheus.

morpheus e' un processo SEPARATO dall'app web: non possono chiamarsi a vicenda, ma
condividono il database. Lui scrive la sua riga di stato (LiveStatus), qui la si
rilegge e la si manda al browser via SSE (Server-Sent Events), il flusso HTTP su cui
il server spinge eventi quando ha qualcosa da dire.

Si usa l'SSE NATIVO di FastAPI (`fastapi.sse`, dalla 0.135): e' lui a occuparsi di
keep-alive, disconnessione del client e chiusura pulita allo spegnimento del server.
"""
import asyncio
from collections.abc import AsyncIterable
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent
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
    silence = (datetime.now(timezone.utc)
               - status.updated_at.replace(tzinfo=timezone.utc)).total_seconds()
    data["active"] = silence < config.LIVE_STALE_AFTER
    if not data["active"]:
        data["message"] = f"morpheus non risponde da {int(silence // 60)} min"
    return data


@router.get("/live")
async def live():
    """Lo stato corrente, una volta sola (comodo per un controllo al volo o da curl)."""
    return _snapshot()


@router.get("/live/stream", response_class=EventSourceResponse)
async def live_stream() -> AsyncIterable[ServerSentEvent]:
    """
    Lo stesso stato, ma in flusso continuo: parte un evento ogni volta che lo stato
    CAMBIA. Il primo si manda sempre, cosi' una pagina appena aperta sa subito
    com'e' messo senza aspettare il primo cambiamento.

    NB niente 'event=': l'evento resta quello di default ('message'), l'unico che il
    browser intercetta con EventSource.onmessage. Con un nome (es. event="live")
    servirebbe addEventListener("live", ...) e onmessage non scatterebbe mai.
    """
    last = None
    while True:
        data = _snapshot()
        if data != last:
            last = data
            yield ServerSentEvent(data=data)
        await asyncio.sleep(config.LIVE_POLL_INTERVAL)
