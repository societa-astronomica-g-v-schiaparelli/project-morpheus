"""
morpheus.py — il direttore d'orchestra della nottata.

Programma a sé che gira sempre e DORME di giorno tra una notte e l'altra.
Ogni ciclo: calcola quando inizia la prossima notte astronomica, dorme fino a
~30 minuti prima, si sveglia, controlla il meteo, congela il piano della serata,
accende la strumentazione, invia lo script di ogni osservazione al suo orario, poi
spegne e torna a dormire fino alla notte successiva.

Avvio (dalla cartella backend/):  ../.venv/bin/python morpheus.py
"""
import asyncio
from astropy.time import Time
import astropy.units as u
import websockets

from db import observations_to_schedule, save_plan, list_slots, get_observation, record_progress
from services.scheduler import plan_campaign
from services.astronomy import night_window
from services.weather import weather_is_favorable
from services import dispatcher

# --- impostazioni ---
WAKE_BEFORE_MIN = 30    # minuti prima dell'inizio notte in cui svegliarsi
NIGHTS_HORIZON = 7      # su quante notti pianificare
MIN_ALTITUDE = 30       # altezza minima di default del target (gradi)


def freeze_plan(date_str):
    """Ripianifica dallo stato ATTUALE del DB e salva ('congela') il piano.
    Il piano e' una previsione, ogni sera lo si rifa' sullo stato reale prima di eseguire."""
    targets = [o.to_target() for o in observations_to_schedule()]
    plan = plan_campaign(targets, Time(date_str),
                         nights=NIGHTS_HORIZON, min_altitude=MIN_ALTITUDE)
    save_plan(plan)
    return plan


async def sleep_until(when_iso):
    """Dorme fino all'istante 'when_iso' (stringa UTC). Se e' gia' passato, non aspetta."""
    delay = (Time(when_iso) - Time.now()).sec
    if delay > 0:
        await asyncio.sleep(delay)


def next_observing_night(now=None):
    """Trova la prossima notte da osservare: ritorna (date_str, wake) dove 'date_str'
    e' la sera di riferimento e 'wake' e' il Time in cui svegliarsi (inizio notte
    meno WAKE_BEFORE_MIN). Prende la sera di oggi se il momento di sveglia non e'
    ancora passato, altrimenti va avanti. Salta le notti bianche."""
    now = now or Time.now()
    day = Time(now.iso[:10])                       # mezzanotte UTC di oggi
    for _ in range(365):                           # cerca in avanti (max ~1 anno)
        night = night_window(day)
        if night is not None:
            night_start, _ = night
            wake = night_start - WAKE_BEFORE_MIN * u.min
            if wake > now:
                return day.iso[:10], wake
        day = day + 1 * u.day
    return None, None


async def run_night(date_str):
    """Esegue UNA nottata: meteo -> congela il piano -> accende -> per ogni slot
    aspetta l'orario e invia lo script -> spegne."""
    print(f"[morpheus] --- nottata {date_str} ---")

    # 1) il meteo vince su tutto: se avverso, si salta la notte
    meteo = weather_is_favorable(date_str)
    if not meteo["favorable"]:
        print(f"[morpheus] meteo avverso ({meteo['reason']}) -> nottata annullata")
        return

    # 2) congela il piano della serata e prendi gli slot di stanotte
    freeze_plan(date_str)
    slots = list_slots(date_str)
    if not slots:
        print("[morpheus] nessuno slot da eseguire stanotte")
        return
    print(f"[morpheus] {len(slots)} slot in programma")

    # 3) una connessione per tutta la notte: accendi, semina, avvia
    async with websockets.connect(dispatcher.INDIGO_WS_URL, open_timeout=5, max_size=None) as ws:
        await dispatcher.setup_devices(ws)
        await dispatcher.inject_prelude(ws)
        await dispatcher.send_startup(ws)

        # 4) invia ogni osservazione al suo orario e aspetta il suo esito
        for slot in slots:
            await sleep_until(slot.start)
            obs = get_observation(slot.observation_id)
            if obs is None:
                continue
            print(f"[morpheus] {slot.start[11:16]} -> {slot.target_name} ({slot.frames} pose)")
            await dispatcher.send_observation(ws, obs)

            # FEEDBACK: aspetta che la sequenza finisca, con timeout generoso legato
            # alla durata prevista dello slot; registra i progressi solo se completata.
            durata = (Time(slot.end) - Time(slot.start)).sec
            esito, _ = await dispatcher.await_sequence(ws, max_seconds=durata * 1.5 + 120)
            if esito == "ok":
                record_progress(slot.observation_id, slot.frames)
                print(f"[morpheus]   completata: +{slot.frames} pose registrate")
            else:
                print(f"[morpheus]   sequenza {esito}: pose NON registrate (verra' ripianificata)")

        # 5) fine notte: metti in sicurezza e spegni
        await dispatcher.send_shutdown(ws)

    print(f"[morpheus] nottata {date_str} conclusa")


async def main_loop():
    """Il ciclo eterno: dormi -> svegliati -> esegui la notte -> ripeti."""
    print("[morpheus] avviato. Veglio, mentre l'osservatorio \"dorme\".")
    while True:
        date_str, wake = next_observing_night()
        if date_str is None:
            print("[morpheus] nessuna notte trovata nell'orizzonte, mi fermo")
            return
        print(f"[morpheus] prossima notte: {date_str} | sveglia: {wake.iso[:16]} UTC")
        await sleep_until(wake.iso)
        try:
            await run_night(date_str)
        except Exception as e:
            print(f"[morpheus] errore nella nottata {date_str}: {e}")


if __name__ == "__main__":
    asyncio.run(main_loop())
