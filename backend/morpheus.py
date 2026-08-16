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

import config
from db import (observations_to_schedule, save_plan, list_slots, get_observation,
                record_progress, log_execution, set_live_status)
from services.scheduler import plan_campaign, overhead_minutes
from services.astronomy import night_window, simulation_epoch
from services.weather import weather_is_favorable
from services import dispatcher


def freeze_plan(date_str):
    """Ripianifica dallo stato ATTUALE del DB e salva ('congela') il piano.
    Il piano e' una previsione, ogni sera lo si rifa' sullo stato reale prima di eseguire."""
    targets = [o.to_target() for o in observations_to_schedule()]
    plan = plan_campaign(targets, Time(date_str),
                         nights=config.NIGHTS_HORIZON, min_altitude=config.DEFAULT_MIN_ALTITUDE)
    save_plan(plan)
    return plan


async def sleep_until(when_iso):
    """Dorme fino all'istante 'when_iso' (stringa UTC). Se e' gia' passato, non aspetta."""
    delay = (Time(when_iso) - Time.now()).sec
    if delay > 0:
        await asyncio.sleep(delay)


def next_observing_night(now=None, skip=None):
    """Trova la prossima notte da osservare: ritorna (date_str, wake) dove 'date_str'
    e' la sera di riferimento e 'wake' e' il Time in cui svegliarsi (inizio notte
    meno WAKE_BEFORE_MIN). Prende la sera di oggi se il momento di sveglia non e'
    ancora passato, altrimenti va avanti. Salta le notti bianche.
    'skip' e' la notte appena conclusa, da non riproporre.
    In SIMULAZIONE si parte subito: la prima notte finta e' quella in corso (o la
    prossima, se quella di adesso e' gia' finita), e non c'e' nessuna sveglia
    anticipata da rispettare."""
    now = now or Time.now()

    if config.SIMULATION_MODE:
        epoch = simulation_epoch()
        cycle = config.SIMULATION_MINUTES + config.SIMULATION_GAP_MINUTES
        for i in range(365):
            start = epoch + i * cycle * u.min
            day_str = (Time(epoch.iso[:10]) + i * u.day).iso[:10]
            # va bene la notte ancora in corso (cosi' si parte subito), tranne se e'
            # quella che abbiamo appena eseguito: altrimenti la rifaremmo all'infinito
            if start + config.SIMULATION_MINUTES * u.min > now and day_str != skip:
                return day_str, start
        return None, None

    day = Time(now.iso[:10])                       # mezzanotte UTC di oggi
    for _ in range(365):                           # cerca in avanti (max ~1 anno)
        night = night_window(day)
        if night is not None:
            night_start, _ = night
            wake = night_start - config.WAKE_BEFORE_MIN * u.min
            if wake > now and day.iso[:10] != skip:
                return day.iso[:10], wake
        day = day + 1 * u.day
    return None, None


def _progress_writer(slot):
    """Costruisce la callback che riversa SEQUENCE_STATE nella riga di stato live.
    Limita la frequenza di scrittura (LIVE_MIN_INTERVAL): INDIGO puo' aggiornare
    molto spesso e non serve scrivere sul database a ogni singolo messaggio."""
    ultimo = [0.0]

    def scrivi(progress):
        now = asyncio.get_event_loop().time()
        if now - ultimo[0] < config.LIVE_MIN_INTERVAL:
            return
        ultimo[0] = now
        set_live_status(
            phase="osservazione", night=slot.night,
            message=f"Ripresa di {slot.target_name} in corso",
            observation_id=slot.observation_id, target_name=slot.target_name,
            slot_start=slot.start, slot_end=slot.end,
            step=progress.get("STEP"),
            progress=progress.get("PROGRESS"), progress_total=progress.get("PROGRESS_TOTAL"),
            exposure=progress.get("EXPOSURE"), exposure_total=progress.get("EXPOSURE_TOTAL"),
        )

    return scrivi


async def run_night(date_str):
    """Esegue UNA nottata: meteo -> congela il piano -> accende -> per ogni slot
    aspetta l'orario e invia lo script -> spegne.
    Lungo tutto il percorso aggiorna la riga di stato live, che l'app web trasmette
    al browser via SSE (i due processi si parlano attraverso il database)."""
    print(f"[morpheus] --- nottata {date_str} ---")

    # 1) il meteo vince su tutto: se avverso, si salta la notte
    set_live_status(phase="meteo", night=date_str, message="Controllo del meteo")
    weather = weather_is_favorable(date_str)
    if not weather["favorable"]:
        print(f"[morpheus] meteo avverso ({weather['reason']}) -> nottata annullata")
        set_live_status(phase="conclusa", night=date_str,
                        message=f"Nottata annullata per meteo avverso: {weather['reason']}")
        return

    # 2) congela il piano della serata e prendi gli slot di stanotte
    freeze_plan(date_str)
    slots = list_slots(date_str)
    if not slots:
        print("[morpheus] nessuno slot da eseguire stanotte")
        set_live_status(phase="conclusa", night=date_str,
                        message="Nessuna osservazione in programma stanotte")
        return
    print(f"[morpheus] {len(slots)} slot in programma")

    # 3) una connessione per tutta la notte: accendi e avvia
    #    (il preludio e' incluso in ogni script inviato, non piu' seminato a parte)
    set_live_status(phase="accensione", night=date_str,
                    message="Accensione della strumentazione")
    async with websockets.connect(config.INDIGO_WS_URL, open_timeout=5, max_size=None,
                                  ping_interval=config.WS_PING_INTERVAL) as ws:
        await dispatcher.send_startup(ws)   # carica il preset (load_config) + accende

        # 4) invia ogni osservazione al suo orario e aspetta il suo esito
        for slot in slots:
            set_live_status(phase="pausa", night=date_str, next_start=slot.start,
                            target_name=slot.target_name,
                            message=f"In attesa: {slot.target_name} alle {slot.start[11:16]} UTC")
            await sleep_until(slot.start)
            obs = get_observation(slot.observation_id)
            if obs is None:
                continue
            print(f"[morpheus] {slot.start[11:16]} -> {slot.target_name} ({slot.frames} pose)")
            set_live_status(phase="osservazione", night=date_str,
                            message=f"Preparazione di {slot.target_name}",
                            observation_id=slot.observation_id, target_name=slot.target_name,
                            slot_start=slot.start, slot_end=slot.end)
            # per un orario FISSO le pose devono partire all'ora esatta chiesta dall'utente:
            # lo slot inizia 'overhead' prima (preparazione), quindi l'ora delle pose e'
            # start + overhead. wait_until la inchioda; per gli altri oggetti si parte subito.
            wait_until = None
            if slot.fixed:
                wait_until = (Time(slot.start) + overhead_minutes() * u.min).iso
            await dispatcher.send_observation(ws, obs, wait_until=wait_until)

            # FEEDBACK: aspetta che la sequenza finisca, con timeout generoso legato
            # alla durata prevista dello slot; registra i progressi solo se completata.
            # La callback riversa gli aggiornamenti nello stato live, posa per posa.
            duration = (Time(slot.end) - Time(slot.start)).sec
            outcome, _ = await dispatcher.await_sequence(
                ws, max_seconds=duration * 1.5 + config.SEQUENCE_TIMEOUT_MARGIN,
                on_progress=_progress_writer(slot))
            log_execution(slot, outcome)   # diario di bordo: com'e' andata, sempre
            if outcome == "ok":
                record_progress(slot.observation_id, slot.frames)
                print(f"[morpheus]   completata: +{slot.frames} pose registrate")
            else:
                print(f"[morpheus]   sequenza {outcome}: pose NON registrate (verra' ripianificata)")

        # 5) fine notte: metti in sicurezza e spegni
        set_live_status(phase="spegnimento", night=date_str,
                        message="Messa in sicurezza e spegnimento")
        await dispatcher.send_shutdown(ws)

    print(f"[morpheus] nottata {date_str} conclusa")
    set_live_status(phase="conclusa", night=date_str, message="Nottata conclusa")


async def main_loop():
    """Il ciclo eterno: dormi -> svegliati -> esegui la notte -> ripeti."""
    print("[morpheus] avviato. Veglio, mentre l'osservatorio \"dorme\".")
    if config.SIMULATION_MODE:
        print(f"[morpheus] ATTENZIONE: modalita' SIMULAZIONE — notti finte da "
              f"{config.SIMULATION_MINUTES} min, vincoli astronomici e meteo scavalcati")
    fatta = None                       # l'ultima notte eseguita, da non ripetere
    while True:
        date_str, wake = next_observing_night(skip=fatta)
        if date_str is None:
            print("[morpheus] nessuna notte trovata nell'orizzonte, mi fermo")
            return
        print(f"[morpheus] prossima notte: {date_str} | sveglia: {wake.iso[:16]} UTC")
        set_live_status(phase="attesa", night=date_str, next_start=wake.iso,
                        message=f"In attesa della notte del {date_str} "
                                f"(sveglia alle {wake.iso[11:16]} UTC)")
        await sleep_until(wake.iso)
        try:
            await run_night(date_str)
        except Exception as e:
            print(f"[morpheus] errore nella nottata {date_str}: {e}")
            set_live_status(phase="fermo", night=date_str,
                            message=f"Errore nella nottata {date_str}: {e}")
        fatta = date_str


if __name__ == "__main__":
    asyncio.run(main_loop())
