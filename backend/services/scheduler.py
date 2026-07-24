from astropy.time import Time
import astropy.units as u
import numpy as np
from services.astronomy import (
    night_window, altitude_curve, visibility_summary, altitude_at, moon_constraint_ok,
)


def rank_by_visibility(targets, date, min_altitude=30):
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


def observation_duration_minutes(target):
    """
    Durata di un'osservazione in minuti: numero di pose per esposizione (in secondi).
    'target' e' un dict con 'frames' (n. pose) ed 'exposition' (secondi per posa).
    (In futuro qui si aggiungera' l'overhead: download, messa a fuoco, cambio filtro.)
    """
    return target["frames"] * target["exposition"] / 60


def _overlaps(start, end, busy):
    """True se l'intervallo [start, end] si sovrappone a uno degli intervalli occupati
    'busy' (lista di coppie (b_start, b_end))."""
    return any(start < b_end and end > b_start for b_start, b_end in busy)


def stays_above_horizon(ra, dec, start, end, floor=0, step_minutes=5):
    """
    True se il target ('ra' ore, 'dec' gradi) resta sopra l'altezza 'floor' (gradi)
    per TUTTA la durata dello slot [start, end]. Serve a bocciare un orario fisso
    fisicamente impossibile (target sotto l'orizzonte = telescopio puntato a terra).
    """
    n = int(round((end - start).sec / 60 / step_minutes)) + 1
    times = start + np.arange(n) * step_minutes * u.min
    return bool(np.min(altitude_at(ra, dec, times)) > floor)


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


def build_schedule(targets, date, min_altitude=30, horizon_limit=0):
    """
    Costruisce una schedule oraria per la notte, in due fasi:
      1) ORARI FISSI (chi ha 'fixed_start', un Time/stringa UTC) inchiodati al loro
         slot, in ordine di arrivo (FIFO). Vincono sui vincoli SOFT (altezza minima,
         Luna, priorita'), ma NON sulla fisica: se il target e' sotto 'horizon_limit'
         durante lo slot e' impossibile e viene rifiutato. Anche due fissi che si
         sovrappongono danno conflitto (il secondo rifiutato). I rifiutati -> 'conflicts'.
      2) LIBERI ordinati per priorita' (rank_by_visibility), che riempiono i buchi
         rimasti senza invadere gli slot fissi.

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
    for t in fixed:
        start = Time(t["fixed_start"])
        end = start + observation_duration_minutes(t) * u.min
        # la fisica vince sull'utente: il target deve stare sopra l'orizzonte nello slot
        if not stays_above_horizon(t["ra"], t["dec"], start, end, floor=horizon_limit):
            conflicts.append({
                "name": t["name"],
                "reason": "target sotto l'orizzonte all'orario fisso richiesto (impossibile)",
            })
            continue
        if _overlaps(start, end, busy):
            conflicts.append({
                "name": t["name"],
                "reason": "orario fisso in conflitto con un altro fisso (FIFO: rifiutato)",
            })
            continue
        scheduled.append({"id": t.get("id"), "name": t["name"], "start": start, "end": end,
                          "duration_minutes": observation_duration_minutes(t),
                          "frames": t["frames"], "fixed": True, "partial": False})
        busy.append((start, end))

    # --- Fase 2: liberi, per priorita', nei buchi ---
    for t in rank_by_visibility(free, date, min_altitude=min_altitude):
        per_frame_min = t["exposition"] / 60

        if t.get("splittable"):
            # split: piazzo quante pose entrano nel primo buco; le altre alla notte dopo
            start, avail = first_gap(t["window_start"], t["window_end"], busy)
            frames_fit = int(avail // per_frame_min) if start is not None else 0
            if frames_fit < 1:
                unplaced.append({"name": t["name"], "remaining_frames": t["frames"],
                                 "reason": "nessun buco stanotte (split rimandato)"})
                continue
            frames_now = min(t["frames"], frames_fit)
            end = start + frames_now * per_frame_min * u.min
        else:
            # intero o niente: cerco il primo buco che contiene tutta la durata
            frames_now = t["frames"]
            start = earliest_free_start(t["window_start"], observation_duration_minutes(t),
                                        busy, t["window_end"])
            if start is None:
                unplaced.append({"name": t["name"],
                                 "reason": "nessun buco libero nella sua finestra stanotte"})
                continue
            end = start + frames_now * per_frame_min * u.min

        # vincolo Luna (opzionale, solo x target liberi): il target deve restare abbastanza
        # lontano dalla Luna per tutta la durata dello slot, altrimenti -> altra notte
        if t.get("moon_check"):
            n = int(round((end - start).sec / 60 / 5)) + 1
            slot_times = start + np.arange(n) * 5 * u.min
            info = moon_constraint_ok(t["ra"], t["dec"], slot_times,
                                      base_angle=t.get("moon_base_angle", 90))
            if not info["ok"]:
                unplaced.append({"name": t["name"], "remaining_frames": t["frames"],
                                 "reason": "troppo vicino alla Luna, spostato ad altra notte"})
                continue

        remaining = t["frames"] - frames_now  # >0 solo se split parziale
        scheduled.append({"id": t.get("id"), "name": t["name"], "start": start, "end": end,
                          "duration_minutes": frames_now * per_frame_min,
                          "frames": frames_now, "fixed": False, "partial": remaining > 0})
        busy.append((start, end))
        if remaining > 0:
            unplaced.append({"name": t["name"], "remaining_frames": remaining,
                             "reason": f"split: restano {remaining} pose per le prossime notti"})

    scheduled.sort(key=lambda e: e["start"].jd)  # ordine cronologico finale
    return {
        "night_start": night_start,
        "night_end": night_end,
        "scheduled": scheduled,
        "unplaced": unplaced,
        "conflicts": conflicts,
    }


def plan_campaign(targets, start_date, nights=7, min_altitude=30, horizon_limit=0):
    """
    Pianifica un insieme di target su piu' notti consecutive (ROLLOVER).
    Scorre 'nights' notti a partire da 'start_date'. Ogni notte esegue build_schedule
    sui target ancora da fare: chi entra e' schedulato quella notte e non si ripropone;
    chi resta (unplaced) viene ritentato la notte successiva.

    Gli orari fissi valgono solo per la loro notte: ogni fisso viene assegnato alla
    notte la cui finestra contiene il suo 'fixed_start' (se nessuna, e' fuori campagna).

    Ritorna:
      - by_night          : lista di {date, schedule} (output di build_schedule per notte)
      - free_unscheduled  : target liberi mai piazzati entro l'orizzonte di 'nights' notti
      - fixed_unschedulable : orari fissi che cadono fuori dalle notti pianificate
    """
    fixed = [t for t in targets if t.get("fixed_start")]
    free = [t for t in targets if not t.get("fixed_start")]

    dates = [start_date + i * u.day for i in range(nights)]
    windows = [night_window(d) for d in dates]

    # smisto ogni orario fisso alla notte la cui finestra contiene il suo istante
    fixed_by_night = {i: [] for i in range(nights)}
    fixed_unschedulable = []
    for t in fixed:
        ts = Time(t["fixed_start"])
        night_i = next((i for i, w in enumerate(windows)
                        if w is not None and w[0] <= ts <= w[1]), None)
        if night_i is None:
            fixed_unschedulable.append({"name": t["name"],
                                        "reason": "orario fisso fuori dalle notti pianificate"})
        else:
            fixed_by_night[night_i].append(t)

    by_night = []
    for i, date in enumerate(dates):
        tonight = fixed_by_night[i] + free
        schedule = build_schedule(tonight, date,
                                  min_altitude=min_altitude, horizon_limit=horizon_limit)
        by_night.append({"date": date, "schedule": schedule})

        # aggiorno il giro dei target 'liberi' per la notte successiva:
        # - completati (piazzati NON parziali) -> escono
        # - split parziali -> restano con le pose rimanenti (remaining_frames)
        # - non piazzati -> restano invariati e riprovano
        completed = {e["name"] for e in schedule["scheduled"] if not e.get("partial")}
        remaining_frames = {u["name"]: u["remaining_frames"]
                            for u in schedule["unplaced"] if "remaining_frames" in u}
        next_free = []
        for t in free:
            if t["name"] in remaining_frames:
                next_free.append({**t, "frames": remaining_frames[t["name"]]})
            elif t["name"] not in completed:
                next_free.append(t)
        free = next_free

        # mi fermo se non resta nulla da fare (ne' liberi ne' fissi futuri)
        if not free and not any(fixed_by_night[j] for j in range(i + 1, nights)):
            break

    return {
        "by_night": by_night,
        "free_unscheduled": free,
        "fixed_unschedulable": fixed_unschedulable,
    }
