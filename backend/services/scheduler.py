from astropy.time import Time
import astropy.units as u
import numpy as np
import config
from services.astronomy import (
    night_window, altitude_curve, visibility_summary, altitude_at, moon_constraint_ok,
)


def rank_by_visibility(targets, date, min_altitude=config.DEFAULT_MIN_ALTITUDE):
    """
    Ordina una lista di target per priorita' di visibilita' nella notte indicata.
    Criterio: fotografare prima cio' che e' visibile per meno tempo, ovvero chi tramonta prima (window_end piu' presto);
    a parita' di tramonto, ha priorita' chi ha altezza media minore (piu' marginale).

    'targets' e' una lista di dict con almeno {name, ra, dec} (ra in ore, dec in gradi).
    'date' e' un Time, ossia il giorno della serata osservativa  (es. Time('2026-07-19')).
    Ritorna la lista dei soli target OSSERVABILI, ciascuno arricchito con il suo
    visibility_summary, ordinata per finestra che finisce prima.
    I non osservabili (mai sopra 'min_altitude' durante la notte) vengono esclusi.
    """
    night = night_window(date)
    if night is None:
        return []  # notte bianca: niente da schedulare
    night_start, night_end = night

    if config.SIMULATION_MODE:
        # In simulazione il cielo non e' un vincolo: la notte finta cade quasi sempre di
        # giorno, quindi con i calcoli veri NESSUN target risulterebbe visibile e non ci
        # sarebbe niente da schedulare. Qui li dichiariamo tutti osservabili per l'intera
        # finestra; l'ordine resta quello di arrivo (FIFO), perche' il criterio di
        # priorita' per visibilita' non avrebbe senso su altezze inventate.
        return [{**t, "observable": True,
                 "window_start": night_start, "window_end": night_end,
                 "transit_time": night_start, "max_altitude": 90.0, "mean_altitude": 90.0,
                 "duration_minutes": (night_end - night_start).sec / 60}
                for t in targets]

    # per ogni target: curva di altitudine nella notte + riassunto di visibilita'.
    # Ogni target puo' avere una sua soglia 'min_altitude'; altrimenti usa quella globale.
    enriched = []
    for t in targets:
        times, altitudes = altitude_curve(t["ra"], t["dec"], night_start, night_end)
        summary = visibility_summary(times, altitudes,
                                     min_altitude=t.get("min_altitude", min_altitude))
        enriched.append({**t, **summary})

    # tengo solo gli osservabili e li ordino con chiave doppia:
    # 1) chi tramonta prima (window_end)  2) a parita', chi sta piu' in basso (mean_altitude)
    observable = [e for e in enriched if e["observable"]]
    observable.sort(key=lambda e: (e["window_end"].jd, e["mean_altitude"]))
    return observable


def total_frames(frames):
    """Pose totali di un'osservazione, sommando su tutti i filtri."""
    return sum(frames.values())


def overhead_minutes():
    """Minuti di preparazione da riservare PRIMA delle pose di ogni slot: slew, attesa
    della cupola, precise_goto, messa a fuoco e avvio della guida. In TEST_MODE lo
    script salta le operazioni lente, quindi la preparazione e' molto piu' corta.
    Letto a ogni chiamata (non a import) cosi' cambiare TEST_MODE ha effetto subito."""
    return config.OVERHEAD_MINUTES_TEST if config.TEST_MODE else config.OVERHEAD_MINUTES


def frames_duration_minutes(target):
    """Durata delle sole POSE in minuti: pose TOTALI (somma su tutti i filtri) per esposizione."""
    return total_frames(target["frames"]) * target["exposition"] / 60


def observation_duration_minutes(target):
    """
    Durata TOTALE di uno slot in minuti: overhead di preparazione + pose.
    'target' ha 'frames' ({filtro: n_pose}) ed 'exposition' (secondi per posa).
    Lo slot occupa il telescopio dall'inizio della preparazione: e' questa la durata
    che conta per il piazzamento sulla timeline.
    """
    return overhead_minutes() + frames_duration_minutes(target)


def take_frames(frames, n, cycle):
    """Prende fino a 'n' pose totali da 'frames' ({filtro: count}) seguendo la rotazione
    (fino a 'cycle' pose per filtro a giro, come le scatta il telescopio). Ritorna
    (taken, remaining): cosa fa questa notte e cosa resta, entrambi dict {filtro: count}."""
    taken = {}
    remaining = {f: c for f, c in frames.items() if c > 0}
    while n > 0 and remaining:
        for f in list(remaining.keys()):
            take = min(cycle, remaining[f], n)
            taken[f] = taken.get(f, 0) + take
            remaining[f] -= take
            n -= take
            if remaining[f] <= 0:
                del remaining[f]
            if n <= 0:
                break
    return taken, remaining


def above_horizon_until(ra, dec, start, end, floor=0, step_minutes=5):
    """
    Fino a QUANDO il target ('ra' ore, 'dec' gradi) resta sopra l'altezza 'floor'
    (gradi) partendo da 'start'. Ritorna l'ultimo istante utile dentro [start, end]:
    'end' se non scende mai, 'start' se e' gia' sotto in partenza.
    Serve a troncare un orario fisso al momento in cui il target tramonta, invece di
    puntare il telescopio a terra.
    """
    if config.SIMULATION_MODE:
        return end          # in simulazione il cielo non vincola: niente tramonti
    n = int(round((end - start).sec / 60 / step_minutes)) + 1
    times = start + np.arange(n) * step_minutes * u.min
    below = np.where(altitude_at(ra, dec, times) <= floor)[0]
    if len(below) == 0:
        return end
    return times[below[0] - 1] if below[0] > 0 else start


def earliest_free_start(desired, duration_minutes, busy, limit):
    """
    Trova il primo istante >= 'desired' in cui un'osservazione lunga
    'duration_minutes' ci sta SENZA sovrapporsi agli intervalli gia' occupati 'busy'.
    Se incontra un intervallo occupato, salta subito dopo di esso e riprova.
    Ritorna il Time di inizio trovato, oppure None se non entra prima di 'limit'.
    """
    start = desired
    moved = True
    while moved:
        moved = False
        for b_start, b_end in busy:
            end = start + duration_minutes * u.min
            if start < b_end and end > b_start:   # sovrapposizione: spostati dopo il blocco
                start = b_end
                moved = True
    if start + duration_minutes * u.min <= limit:
        return start
    return None


def first_gap(window_start, window_end, busy):
    """
    Trova il PRIMO buco libero dentro [window_start, window_end] rispetto agli
    intervalli occupati 'busy', e quanto e' grande. Serve allo split: dentro quel
    buco ci infiliamo quante pose ci stanno.
    Ritorna (start, minuti_disponibili), oppure (None, 0) se non c'e' spazio.
    """
    start = window_start
    moved = True
    while moved:                       # spingo 'start' fuori da eventuali blocchi occupati
        moved = False
        for b_start, b_end in busy:
            if b_start <= start < b_end:
                start = b_end
                moved = True
    if start >= window_end:
        return None, 0
    # il buco arriva fino al prossimo blocco che inizia dopo 'start', o alla fine finestra
    limit = window_end
    for b_start, _ in busy:
        if start < b_start < limit:
            limit = b_start
    return start, (limit - start).sec / 60


def build_schedule(targets, date, min_altitude=config.DEFAULT_MIN_ALTITUDE, horizon_limit=config.HORIZON_LIMIT):
    """
    Costruisce una schedule oraria per la notte, in due fasi:
      1) ORARI FISSI (chi ha 'fixed_start', un Time/stringa UTC) inchiodati al loro
         slot, in ordine di arrivo (FIFO). Vincono sui vincoli SOFT (altezza minima,
         Luna, priorita'), ma NON sulla fisica: se il target e' sotto 'horizon_limit'
         quando si comincia e' impossibile e viene rifiutato. Anche due fissi che si
         sovrappongono danno conflitto (il secondo rifiutato). I rifiutati -> 'conflicts'.
         Se le pose non entrano tutte prima della fine della notte (o del tramonto del
         target, o del prossimo fisso): un fisso SPLITTABILE ne fa quante ne entrano e
         rimanda le altre (-> 'unplaced' con 'remaining_frames', che plan_campaign
         ripropone alla STESSA ORA la notte dopo); uno non splittabile viene rifiutato
         dicendo quante pose ci sarebbero state.
      2) LIBERI ordinati per priorita' (rank_by_visibility), che riempiono i buchi
         rimasti senza invadere gli slot fissi.

    OGNI slot riserva 'overhead_minutes()' di preparazione PRIMA delle pose (slew,
    cupola, fuoco, guida). Quindi lo slot occupa [start, end] ma le pose partono a
    'frames_start' = start + overhead. Per un orario FISSO l'istante scelto dall'utente
    e' quando partono le POSE: lo slot arretra di conseguenza a 'fixed_start - overhead'.

    'min_altitude' (soft) e' la soglia di comodita', usata SOLO per i liberi.
    'horizon_limit' (hard) e' il limite fisico dell'orizzonte, usato ANCHE per i fissi.
    Ogni target ha 'frames' ed 'exposition' per la durata; tutti anche 'ra'/'dec'.
    Ritorna un dict: night_start, night_end, scheduled (cronologico), unplaced, conflicts.
    """
    night = night_window(date)
    if night is None:
        return {"night_start": None, "night_end": None,
                "scheduled": [], "unplaced": [], "conflicts": []}
    night_start, night_end = night

    fixed = [t for t in targets if t.get("fixed_start")]
    free = [t for t in targets if not t.get("fixed_start")]

    scheduled = []
    busy = []        # intervalli gia' occupati (fissi + liberi piazzati)
    unplaced = []
    conflicts = []

    # --- Fase 1: orari fissi, in ordine di arrivo (FIFO) ---
    overhead = overhead_minutes()
    for t in fixed:
        # l'orario chiesto dall'utente e' quando partono le POSE: lo slot arretra
        # per lasciare spazio alla preparazione
        frames_start = Time(t["fixed_start"])
        start = frames_start - overhead * u.min
        per_frame_min = t["exposition"] / 60
        wanted = total_frames(t["frames"])

        # (a) la preparazione puo' avvenire al crepuscolo, ma non prima che la notte
        #     (finestra osservativa) sia cominciata: altrimenti lo slot non ci sta davvero
        if start < night_start:
            late_by = round((night_start - start).sec / 60)
            conflicts.append({
                "name": t["name"],
                "reason": (f"orario fisso troppo presto: servono {overhead} min di preparazione "
                           f"prima delle pose, sposta l'inizio di almeno {late_by} min"),
            })
            continue

        # (b) la fisica vince sull'utente: se il target e' gia' sotto l'orizzonte quando
        #     si comincia, l'osservazione e' impossibile (telescopio puntato a terra).
        #     In simulazione si scavalca, come tutti gli altri vincoli astronomici.
        if not config.SIMULATION_MODE and \
                float(altitude_at(t["ra"], t["dec"], start)) <= horizon_limit:
            conflicts.append({
                "name": t["name"],
                "reason": "target sotto l'orizzonte all'orario fisso richiesto (impossibile)",
            })
            continue

        # (c) l'inizio non puo' cadere DENTRO uno slot fisso gia' assegnato (FIFO)
        if any(b_start <= start < b_end for b_start, b_end in busy):
            conflicts.append({
                "name": t["name"],
                "reason": "orario fisso in conflitto con un altro fisso (FIFO: rifiutato)",
            })
            continue

        # (d) fin dove si puo' arrivare stanotte, e cosa ci ferma: la fine della notte,
        #     il tramonto del target, o il prossimo orario fisso gia' assegnato
        limit, cause = night_end, "della fine della notte"
        horizon_end = above_horizon_until(t["ra"], t["dec"], start, night_end,
                                          floor=horizon_limit)
        if horizon_end < limit:
            limit, cause = horizon_end, "del tramonto del target"
        for b_start, _ in busy:
            if start < b_start < limit:
                limit, cause = b_start, "di un altro orario fisso gia' assegnato (FIFO)"

        frames_fit = max(int((limit - frames_start).sec / 60 // per_frame_min), 0)

        if frames_fit >= wanted:
            frames_now, remaining = t["frames"], {}
        elif not t.get("splittable"):
            # non spezzabile: o ci sta tutto, o si rifiuta dicendo quanto ci starebbe
            conflicts.append({
                "name": t["name"],
                "reason": (f"non c'e' abbastanza tempo prima di {cause}: all'orario fisso "
                           f"richiesto ci starebbero solo {frames_fit} pose delle {wanted}"),
            })
            continue
        elif frames_fit < 1:
            unplaced.append({"name": t["name"], "remaining_frames": t["frames"],
                             "reason": "nessuno spazio all'orario fisso stanotte (rimandato)"})
            continue
        else:
            # spezzabile: stanotte quante ne entrano, il resto alla stessa ora la notte dopo
            frames_now, remaining = take_frames(t["frames"], frames_fit, config.FRAMES_PER_CYCLE)

        end = frames_start + total_frames(frames_now) * per_frame_min * u.min
        scheduled.append({"id": t.get("id"), "name": t["name"], "start": start,
                          "frames_start": frames_start, "end": end,
                          "duration_minutes": overhead + total_frames(frames_now) * per_frame_min,
                          "frames": frames_now, "fixed": True, "partial": bool(remaining)})
        busy.append((start, end))
        if remaining:
            unplaced.append({
                "name": t["name"], "remaining_frames": remaining,
                "reason": (f"split: restano {total_frames(remaining)} pose, "
                           f"alla stessa ora nelle prossime notti"),
            })

    # --- Fase 2: liberi, per priorita', nei buchi ---
    for t in rank_by_visibility(free, date, min_altitude=min_altitude):
        per_frame_min = t["exposition"] / 60

        if t.get("splittable"):
            # split: piazzo quante pose (in rotazione) entrano nel primo buco; le altre dopo.
            # Anche una ripresa parziale costa la sua preparazione: le pose possibili si
            # contano sul tempo che AVANZA dopo l'overhead.
            start, avail = first_gap(t["window_start"], t["window_end"], busy)
            frames_fit = int((avail - overhead) // per_frame_min) if start is not None else 0
            if frames_fit < 1:
                unplaced.append({"name": t["name"], "remaining_frames": t["frames"],
                                 "reason": "nessun buco stanotte (split rimandato)"})
                continue
            frames_now, remaining = take_frames(t["frames"], frames_fit, config.FRAMES_PER_CYCLE)
        else:
            # intero o niente: cerco il primo buco che contiene tutta la durata
            frames_now, remaining = t["frames"], {}
            start = earliest_free_start(t["window_start"], observation_duration_minutes(t),
                                        busy, t["window_end"])
            if start is None:
                unplaced.append({"name": t["name"],
                                 "reason": "nessun buco libero nella sua finestra stanotte"})
                continue

        frames_start = start + overhead * u.min      # le pose partono dopo la preparazione
        end = frames_start + total_frames(frames_now) * per_frame_min * u.min

        # vincolo Luna (opzionale, solo x target liberi): il target deve restare abbastanza
        # lontano dalla Luna per tutta la durata dello slot, altrimenti -> altra notte
        if t.get("moon_check") and not config.SIMULATION_MODE:
            n = int(round((end - start).sec / 60 / 5)) + 1
            slot_times = start + np.arange(n) * 5 * u.min
            info = moon_constraint_ok(t["ra"], t["dec"], slot_times,
                                      base_angle=t.get("moon_base_angle", 90))
            if not info["ok"]:
                unplaced.append({"name": t["name"], "remaining_frames": t["frames"],
                                 "reason": "troppo vicino alla Luna, spostato ad altra notte"})
                continue

        scheduled.append({"id": t.get("id"), "name": t["name"], "start": start,
                          "frames_start": frames_start, "end": end,
                          "duration_minutes": overhead + total_frames(frames_now) * per_frame_min,
                          "frames": frames_now, "fixed": False, "partial": bool(remaining)})
        busy.append((start, end))
        if remaining:
            unplaced.append({"name": t["name"], "remaining_frames": remaining,
                             "reason": f"split: restano {total_frames(remaining)} pose per le prossime notti"})

    scheduled.sort(key=lambda e: e["start"].jd)  # ordine cronologico finale
    return {
        "night_start": night_start,
        "night_end": night_end,
        "scheduled": scheduled,
        "unplaced": unplaced,
        "conflicts": conflicts,
    }


def anchor_to_night(fixed_start, window):
    """
    Porta l'ORA DEL GIORNO di un orario fisso dentro la finestra di una notte.
    Della richiesta conta l'ora (es. 23:00 UTC): la data dice solo da quando in poi
    l'osservazione puo' partire, mentre l'ora si ripete su ogni notte utile — e' cosi'
    che un fisso spezzato riparte sempre allo stesso orario le notti successive.
    Ritorna il Time di quella notte, o None se la notte non contiene quell'ora.
    """
    if window is None:
        return None
    night_start, night_end = window
    clock = Time(fixed_start).iso[11:]                      # "HH:MM:SS.sss"
    moment = Time(f"{night_start.iso[:10]} {clock}")
    if moment < night_start:
        moment = moment + 1 * u.day    # le ore dopo mezzanotte sono del giorno seguente
    return moment if night_start <= moment <= night_end else None


def plan_campaign(targets, start_date, nights=config.NIGHTS_HORIZON,
                  min_altitude=config.DEFAULT_MIN_ALTITUDE, horizon_limit=config.HORIZON_LIMIT):
    """
    Pianifica un insieme di target su piu' notti consecutive (ROLLOVER).
    Scorre 'nights' notti a partire da 'start_date'. Ogni notte esegue build_schedule
    sui target ancora da fare: chi entra e' schedulato quella notte e non si ripropone;
    chi resta (unplaced) viene ritentato la notte successiva.

    Gli ORARI FISSI sono ancorati per ORA DEL GIORNO (vedi anchor_to_night): la data
    della richiesta vale come "non prima di", l'ora invece si ripete. Un fisso
    splittabile che non finisce in una notte riprende ALLA STESSA ORA quella dopo,
    mantenendo la sua precedenza FIFO (era arrivato prima delle richieste successive).
    Un fisso non splittabile che viene rifiutato non viene ritentato.

    Ritorna:
      - by_night          : lista di {date, schedule} (output di build_schedule per notte)
      - free_unscheduled  : target liberi mai piazzati entro l'orizzonte di 'nights' notti
      - fixed_unschedulable : orari fissi rimasti incompiuti a fine campagna
    """
    free = [t for t in targets if not t.get("fixed_start")]
    # i fissi restano nell'ordine di arrivo (FIFO): le continuazioni conservano cosi'
    # la precedenza sulle richieste inserite dopo di loro
    pending_fixed = [dict(t) for t in targets if t.get("fixed_start")]
    ever_placed = set()

    dates = [start_date + i * u.day for i in range(nights)]
    windows = [night_window(d) for d in dates]

    by_night = []
    for i, date in enumerate(dates):
        # quali fissi tocca stanotte: quelli la cui ora cade in questa notte e la cui
        # data di partenza e' gia' arrivata
        tonight_fixed = []
        for t in pending_fixed:
            moment = anchor_to_night(t["fixed_start"], windows[i])
            if moment is None or moment < Time(t["fixed_start"]) - 1 * u.s:
                continue
            tonight_fixed.append({**t, "fixed_start": moment.iso})
        tonight_names = {t["name"] for t in tonight_fixed}

        schedule = build_schedule(tonight_fixed + free, date,
                                  min_altitude=min_altitude, horizon_limit=horizon_limit)
        by_night.append({"date": date, "schedule": schedule})

        # com'e' andata stanotte: chi ha finito, chi ha pose rimaste
        completed = {e["name"] for e in schedule["scheduled"] if not e.get("partial")}
        remaining_frames = {u["name"]: u["remaining_frames"]
                            for u in schedule["unplaced"] if "remaining_frames" in u}
        ever_placed |= {e["name"] for e in schedule["scheduled"]}

        # giro dei LIBERI per la notte successiva:
        # - completati (piazzati NON parziali) -> escono
        # - split parziali -> restano con le pose rimanenti (remaining_frames)
        # - non piazzati -> restano invariati e riprovano
        next_free = []
        for t in free:
            if t["name"] in remaining_frames:
                next_free.append({**t, "frames": remaining_frames[t["name"]]})
            elif t["name"] not in completed:
                next_free.append(t)
        free = next_free

        # giro dei FISSI: come sopra, ma chi non e' splittabile non si ritenta
        # (e' stato rifiutato definitivamente, il motivo e' gia' in 'conflicts')
        next_fixed = []
        for t in pending_fixed:
            if t["name"] not in tonight_names:
                next_fixed.append(t)                       # stanotte non era di turno
            elif t["name"] in remaining_frames:
                next_fixed.append({**t, "frames": remaining_frames[t["name"]]})
            elif t["name"] in completed:
                pass                                       # finito
            elif t.get("splittable"):
                next_fixed.append(t)                       # niente spazio: riprova
        pending_fixed = next_fixed

        if not free and not pending_fixed:   # non resta piu' nulla da fare
            break

    fixed_unschedulable = [
        {"name": t["name"], "remaining_frames": t["frames"],
         "reason": ("pose rimaste oltre l'orizzonte di pianificazione"
                    if t["name"] in ever_placed
                    else "orario fisso fuori dalle notti pianificate")}
        for t in pending_fixed
    ]

    return {
        "by_night": by_night,
        "free_unscheduled": free,
        "fixed_unschedulable": fixed_unschedulable,
    }
