from astropy.coordinates import EarthLocation, SkyCoord, AltAz, get_sun
from astropy.time import Time
from typing import cast
import astropy.units as u
import numpy as np


OBSERVATORY = EarthLocation(lat=45.868523 * u.deg, lon=8.770190 * u.deg, height=1226 * u.m)


def altitude_at(ra, dec, time):
    """
    Date le coordinate 'ra' (in ore), 'dec' (in gradi) di un target e un istante di tempo
    ritorna la sua altitudine in gradi dall'orizzonte rispetto al punto di vista predefinito (l'osservatorio)  
    """
    target = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    pov = AltAz(obstime=time, location=OBSERVATORY)
    return cast(float, target.transform_to(pov).alt.deg)


def altitude_curve(ra, dec, start, end, step_minutes=10):
    """
    Costruisce la curva di altitudine di un target su un intervallo di tempo.
    Campiona da 'start' a 'end' ogni 'step_minutes' minuti e chiede a 'altitude_at'
    l'altitudine in ciascun istante.
    """
    total_minutes = (end - start).sec / 60
    n_samples = round(total_minutes / step_minutes) + 1 # Uso 'round' perchè il troncamento potrebbe perdere l'ultimo campione

    times = start + np.arange(n_samples) * step_minutes * u.min

    altitudes = altitude_at(ra, dec, times)
    return times, altitudes


def visibility_summary(times, altitudes, min_altitude=30):
    """
    Dai risultati di 'altitude_curve' ricava i numeri che servono allo scheduler.
    'min_altitude' e' la soglia (in gradi) sotto cui il target non conta come osservabile.
    Ritorna un dizionario con:
      - transit_time    : istante di altezza massima (il picco della curva)
      - max_altitude    : quel valore massimo, in gradi
      - observable      : True se la curva supera la soglia in almeno un istante
      - window_start/end : primo e ultimo istante sopra la soglia
      - duration_minutes : durata della finestra osservabile, in minuti
    Assume una sola finestra continua nella notte (vero per un target che sorge e
    tramonta una volta): window_start/end sono gli estremi del tratto sopra soglia.
    """
    altitudes = np.asarray(altitudes)

    # transito: il punto piu' alto della curva
    i_max = int(np.argmax(altitudes))
    transit_time = times[i_max]
    max_altitude = float(altitudes[i_max])

    # maschera booleana: True dove la curva sta sopra la soglia
    above = altitudes >= min_altitude
    observable = bool(above.any())

    if not observable:
        return {
            "observable": False,
            "transit_time": transit_time,
            "max_altitude": max_altitude,
            "window_start": None,
            "window_end": None,
            "duration_minutes": 0.0,
        }

    # gli indici degli istanti sopra soglia; il primo e l'ultimo delimitano la finestra
    idx = np.where(above)[0]
    window_start = times[idx[0]]
    window_end = times[idx[-1]]
    duration_minutes = (window_end - window_start).sec / 60

    return {
        "observable": True,
        "transit_time": transit_time,
        "max_altitude": max_altitude,
        "window_start": window_start,
        "window_end": window_end,
        "duration_minutes": duration_minutes,
    }


def sun_altitude(time):
    """
    Come 'altitude_at', ma per il Sole: la sua posizione la fornisce Astropy con
    get_sun (il Sole si muove, quindi va chiesta a ogni istante). 'time' puo'
    essere un singolo Time o un array -> ritorna un singolo valore o un array.
    """
    sun = get_sun(time)
    pov = AltAz(obstime=time, location=OBSERVATORY)
    return cast(float, sun.transform_to(pov).alt.deg)


def night_window(date, twilight_deg=-18, step_minutes=2):
    """
    Trova inizio e fine della notte per una data notte.
    'date' e' un Time del giorno della sera (es. Time('2026-07-19')).
    'twilight_deg' e' la profondita' del Sole sotto l'orizzonte che consideriamo
    "buio": -18 = crepuscolo astronomico (cielo davvero scuro), -12 nautico, -6 civile.
    Campiona l'altezza del Sole da mezzogiorno UTC del giorno a mezzogiorno del
    giorno dopo (cosi' una notte intera ci sta dentro) e prende il tratto in cui
    il Sole sta sotto la soglia.
    Ritorna (night_start, night_end) come Time, oppure None se il Sole non scende
    mai sotto la soglia (notti bianche a latitudini alte).
    """
    # mezzogiorno UTC del giorno indicato: il Sole e' alto, siamo lontani dalla notte
    noon = Time(date.iso.split()[0] + " 12:00:00")
    times = noon + np.arange(0, 24 * 60 + 1, step_minutes) * u.min

    below = sun_altitude(times) <= twilight_deg
    idx = np.where(below)[0]
    if len(idx) == 0:
        return None

    return times[idx[0]], times[idx[-1]]