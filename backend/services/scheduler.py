from astropy.time import Time
import astropy.units as u
import numpy as np
from services.astronomy import night_window, altitude_curve, visibility_summary, altitude_at


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

    # per ogni target: curva di altitudine nella notte + riassunto di visibilita'
    enriched = []
    for t in targets:
        times, altitudes = altitude_curve(t["ra"], t["dec"], night_start, night_end)
        summary = visibility_summary(times, altitudes, min_altitude=min_altitude)
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
        scheduled.append({"name": t["name"], "start": start, "end": end,
                          "duration_minutes": observation_duration_minutes(t), "fixed": True})
        busy.append((start, end))

    # --- Fase 2: liberi, per priorita', nei buchi ---
    for t in rank_by_visibility(free, date, min_altitude=min_altitude):
        duration = observation_duration_minutes(t)
        start = earliest_free_start(t["window_start"], duration, busy, t["window_end"])
        if start is None:
            unplaced.append({"name": t["name"],
                             "reason": "nessun buco libero nella sua finestra stanotte"})
            continue
        end = start + duration * u.min
        scheduled.append({"name": t["name"], "start": start, "end": end,
                          "duration_minutes": duration, "fixed": False})
        busy.append((start, end))

    scheduled.sort(key=lambda e: e["start"].jd)  # ordine cronologico finale
    return {
        "night_start": night_start,
        "night_end": night_end,
        "scheduled": scheduled,
        "unplaced": unplaced,
        "conflicts": conflicts,
    }
