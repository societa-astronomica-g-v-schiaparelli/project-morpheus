"""
La modalita' SIMULAZIONE.

Serve a collaudare tutta la catena senza aspettare che faccia buio: il calendario
viene compresso (ogni "giorno" diventa una finestra di pochi minuti) e i vincoli che
dipendono dal cielo vero vengono scavalcati, perche' una notte finta cade quasi sempre
di giorno e altrimenti non risulterebbe osservabile nulla.
L'istante zero e' fissato dalla fixture 'simulation', cosi' i test sono ripetibili.
"""
from astropy.time import Time
import astropy.units as u

import config
import morpheus
from services.astronomy import night_window
from services.scheduler import (
    above_horizon_until, build_schedule, plan_campaign, rank_by_visibility, total_frames,
)
from services.weather import weather_is_favorable

DENEB = {"ra": 20.690, "dec": 45.28}
ANTARES = {"ra": 16.49, "dec": -26.43}


def target(name, coord, frames, **extra):
    return {"name": name, **coord, "frames": frames, "exposition": 60, **extra}


# ============================================================ il calendario compresso

def test_first_simulated_night_starts_now(simulation):
    """La notte simulata comincia all'istante zero e dura SIMULATION_MINUTES."""
    inizio, fine = night_window(Time(simulation.iso[:10]))
    assert abs((inizio - simulation).sec) < 1
    assert abs((fine - inizio).sec / 60 - config.SIMULATION_MINUTES) < 0.01


def test_following_days_are_consecutive_windows(simulation):
    """Ogni giorno del calendario diventa la finestra successiva, separata dalla pausa."""
    giorno0 = Time(simulation.iso[:10])
    inizio0, _ = night_window(giorno0)
    inizio1, _ = night_window(giorno0 + 1 * u.day)
    atteso = config.SIMULATION_MINUTES + config.SIMULATION_GAP_MINUTES
    assert abs((inizio1 - inizio0).sec / 60 - atteso) < 0.01


def test_days_before_the_start_do_not_exist(simulation):
    """Prima dell'istante zero non c'e' nessuna notte simulata."""
    assert night_window(Time(simulation.iso[:10]) - 1 * u.day) is None


def test_real_sky_comes_back_when_simulation_is_off():
    """Senza simulazione si torna alla notte astronomica vera."""
    inizio, fine = night_window(Time("2026-08-01"))
    assert inizio.iso[11:16] == "21:04"
    assert (fine - inizio).sec / 3600 > 4


# ============================================================ i vincoli scavalcati

def test_every_target_is_observable_in_simulation(simulation):
    """Anche un target sotto l'orizzonte risulta osservabile: e' una prova, non il cielo."""
    ordinati = rank_by_visibility([target("A", ANTARES, {"L": 1})],
                                  Time(simulation.iso[:10]))
    assert len(ordinati) == 1 and ordinati[0]["observable"]


def test_ranking_keeps_arrival_order_in_simulation(simulation):
    """Senza altezze vere la priorita' per visibilita' non ha senso: vale il FIFO."""
    ordinati = rank_by_visibility([target("Primo", ANTARES, {"L": 1}),
                                   target("Secondo", DENEB, {"L": 1})],
                                  Time(simulation.iso[:10]))
    assert [t["name"] for t in ordinati] == ["Primo", "Secondo"]


def test_nothing_ever_sets_in_simulation(simulation):
    """Niente tramonti: gli slot non vengono troncati dal cielo."""
    inizio, fine = night_window(Time(simulation.iso[:10]))
    assert above_horizon_until(ANTARES["ra"], ANTARES["dec"], inizio, fine) == fine


def test_moon_never_blocks_in_simulation(simulation):
    """Il vincolo Luna e' scavalcato: altrimenti bloccherebbe prove a caso."""
    s = build_schedule([target("L", ANTARES, {"L": 5}, moon_check=True, moon_base_angle=180)],
                       Time(simulation.iso[:10]))
    assert len(s["scheduled"]) == 1


def test_weather_never_blocks_in_simulation(simulation):
    """Una giornata nuvolosa non deve far abortire una prova."""
    verdetto = weather_is_favorable(simulation.iso[:10])
    assert verdetto["favorable"] and "simulazione" in verdetto["reason"]


# ============================================================ pianificare in simulazione

def test_a_plan_fits_inside_the_simulated_night(simulation):
    """Le osservazioni stanno dentro la finestra breve, preparazione compresa."""
    giorno = Time(simulation.iso[:10])
    inizio, fine = night_window(giorno)
    s = build_schedule([target("A", DENEB, {"L": 5}), target("B", ANTARES, {"L": 5})], giorno)
    assert len(s["scheduled"]) == 2
    for e in s["scheduled"]:
        assert inizio <= e["start"] and e["end"] <= fine


def test_split_rolls_over_simulated_nights(simulation):
    """Lo split multi-notte si collauda in minuti invece che in giorni."""
    piano = plan_campaign([target("M", DENEB, {"L": 200}, splittable=True)],
                          Time(simulation.iso[:10]), nights=5)
    righe = [e for n in piano["by_night"] for e in n["schedule"]["scheduled"]]
    fatte = sum(total_frames(e["frames"]) for e in righe)
    rimaste = sum(total_frames(t["frames"]) for t in piano["free_unscheduled"])
    assert len(righe) > 1                      # si e' davvero spezzato su piu' notti
    assert fatte + rimaste == 200


# ============================================================ morpheus parte subito

def test_morpheus_starts_immediately(simulation):
    """Nessuna attesa: la prima notta utile e' quella in corso."""
    date_str, wake = morpheus.next_observing_night(now=simulation)
    assert date_str == simulation.iso[:10]
    assert abs((wake - simulation).sec) < 1


def test_morpheus_moves_on_after_a_night(simulation):
    """La notte appena eseguita non viene riproposta: altrimenti si ripeterebbe all'infinito."""
    prima, _ = morpheus.next_observing_night(now=simulation)
    dopo, quando = morpheus.next_observing_night(now=simulation, skip=prima)
    assert dopo != prima
    atteso = config.SIMULATION_MINUTES + config.SIMULATION_GAP_MINUTES
    assert abs((quando - simulation).sec / 60 - atteso) < 0.01
