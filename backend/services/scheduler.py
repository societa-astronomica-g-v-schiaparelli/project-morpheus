from astropy.time import Time
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
