import urllib.request
import json
from astropy.time import Time
from services.astronomy import night_window, OBSERVATORY


def fetch_forecast(start_date, end_date, latitude, longitude):
    """
    Scarica da open-meteo la previsione oraria (nuvolosita' % e pioggia mm) per
    l'intervallo di date [start_date, end_date] (stringhe 'YYYY-MM-DD'), in UTC.
    Ritorna il JSON grezzo. Puo' lanciare un'eccezione se la rete non risponde.
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&hourly=cloud_cover,precipitation"
        f"&start_date={start_date}&end_date={end_date}&timezone=UTC"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def night_hours(forecast, night_start, night_end):
    """
    Dalla previsione oraria tiene solo le ore che cadono DENTRO la notte
    [night_start, night_end] (di giorno il cielo non ci interessa).
    Ritorna due liste allineate: (nuvolosita', pioggia) per quelle ore.
    """
    times = forecast["hourly"]["time"]            # es. '2026-07-19T21:00'
    clouds = forecast["hourly"]["cloud_cover"]
    precip = forecast["hourly"]["precipitation"]
    keep = [i for i, ts in enumerate(times)
            if night_start <= Time(ts.replace("T", " ")) <= night_end]
    return [clouds[i] for i in keep], [precip[i] for i in keep]


def evaluate_weather(clouds, precip, max_cloud=60, max_precip=0.1):
    """
    Decide se la notte e' favorevole all'osservazione, date le liste orarie di
    nuvolosita' (%) e pioggia (mm). Avversa se la nuvolosita' media supera
    'max_cloud'% oppure se cade piu' di 'max_precip' mm di pioggia in totale.
    """
    if not clouds:
        return {"favorable": True, "reason": "nessun dato per le ore notturne"}
    avg_cloud = sum(clouds) / len(clouds)
    total_precip = sum(precip)
    favorable = avg_cloud <= max_cloud and total_precip <= max_precip
    return {
        "favorable": favorable,
        "avg_cloud": round(avg_cloud, 1),
        "total_precip": round(total_precip, 2),
        "reason": "cielo sereno" if favorable else "meteo avverso (nuvole o pioggia)",
    }


def weather_is_favorable(date):
    """
    Verdetto meteo per la notte 'date' (Time o stringa 'YYYY-MM-DD') all'osservatorio.
    Se le previsioni non sono raggiungibili NON blocca (favorable=True) ma lo segnala:
    meglio non fermare le osservazioni solo perche' il servizio meteo e' giu'.
    """
    night = night_window(Time(date))
    if night is None:
        return {"favorable": True, "reason": "notte bianca: niente da osservare"}
    night_start, night_end = night

    try:
        forecast = fetch_forecast(
            night_start.iso[:10], night_end.iso[:10],
            OBSERVATORY.lat.deg, OBSERVATORY.lon.deg,
        )
    except Exception as e:
        return {"favorable": True, "reason": f"previsioni non disponibili ({e}), non blocco"}

    clouds, precip = night_hours(forecast, night_start, night_end)
    return evaluate_weather(clouds, precip)
