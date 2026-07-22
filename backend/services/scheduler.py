from astropy.time import Time
import astropy.units as u
from services.astronomy import night_window, altitude_curve, visibility_summary


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


def build_schedule(targets, date, min_altitude=30):
    """
    Costruisce una schedule oraria per la notte: prende i target ordinati per
    priorita' (rank_by_visibility) e li piazza uno dopo l'altro a partire
    dall'inizio della notte, senza sovrapposizioni (un solo telescopio).

    Ogni target deve avere anche 'frames' ed 'exposition' per calcolarne la durata.
    Un'osservazione viene piazzata solo se la sua durata sta dentro la finestra di
    visibilita' del target (deve finire prima che il target scenda sotto soglia) e
    dentro la notte. Altrimenti finisce tra i 'unplaced' (candidati per un'altra notte).

    Ritorna un dict:
      - night_start / night_end : confini della notte
      - scheduled : lista di {name, start, end, duration_minutes} in ordine di esecuzione
      - unplaced  : lista di {name, reason} non piazzati stanotte
    """
    night = night_window(date)
    if night is None:
        return {"night_start": None, "night_end": None, "scheduled": [], "unplaced": []}
    night_start, night_end = night

    ranked = rank_by_visibility(targets, date, min_altitude=min_altitude)

    scheduled = []
    unplaced = []
    cursor = night_start  # prima ora libera del telescopio

    for t in ranked:
        duration = observation_duration_minutes(t)
        # non posso iniziare prima che il target sia osservabile ne' prima che il telescopio sia libero
        start = max(cursor, t["window_start"])
        end = start + duration * u.min

        # deve finire prima che il target tramonti (esca dalla finestra) e prima dell'alba
        if end <= t["window_end"] and end <= night_end:
            scheduled.append({
                "name": t["name"],
                "start": start,
                "end": end,
                "duration_minutes": duration,
            })
            cursor = end  # il telescopio si libera a fine osservazione
        else:
            unplaced.append({
                "name": t["name"],
                "reason": "non entra nella finestra di visibilita' stanotte",
            })

    return {
        "night_start": night_start,
        "night_end": night_end,
        "scheduled": scheduled,
        "unplaced": unplaced,
    }
