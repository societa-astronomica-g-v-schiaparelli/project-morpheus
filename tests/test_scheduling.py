"""
Le logiche dello scheduler, caso per caso.

Notte di riferimento: 1 agosto 2026, buio 21:04 -> 01:58 UTC (circa 4h54).
Deneb non tramonta mai dalla latitudine dell'osservatorio: si usa quando il target
deve solo "esserci". Antares invece tramonta durante la notte: serve ai casi di
troncamento. Le durate sono scelte in modo che 1 posa da 60s = 1 minuto di slot.
"""
from astropy.time import Time
import astropy.units as u
import pytest

import config
from services.astronomy import night_window
from services.scheduler import (
    anchor_to_night, above_horizon_until, build_schedule, frames_duration_minutes,
    observation_duration_minutes, overhead_minutes, plan_campaign, rank_by_visibility,
    take_frames, total_frames,
)

DATE = Time("2026-08-01")
DENEB = {"ra": 20.690, "dec": 45.28}      # circumpolare: sempre sopra l'orizzonte
VEGA = {"ra": 18.615, "dec": 38.78}       # alta tutta la notte
ANTARES = {"ra": 16.49, "dec": -26.43}    # bassa, tramonta nel corso della notte
OVH = config.OVERHEAD_MINUTES


@pytest.fixture(scope="module")
def night():
    """Inizio e fine del buio della notte di riferimento."""
    return night_window(DATE)


def target(name, coord, frames, **extra):
    """Scorciatoia per costruire un target da dare allo scheduler."""
    return {"name": name, **coord, "frames": frames, "exposition": 60, **extra}


def slots(plan):
    """Tutte le righe di un piano multi-notte: (notte, ora pose, pose, parziale)."""
    return [(n["date"].iso[:10], e["frames_start"].iso[11:16],
             total_frames(e["frames"]), e["partial"])
            for n in plan["by_night"] for e in n["schedule"]["scheduled"]]


# ============================================================ durate e overhead

def test_duration_is_overhead_plus_exposures():
    """La durata di uno slot e' la preparazione piu' le pose, non solo le pose."""
    t = target("X", DENEB, {"L": 10, "R": 5})
    assert frames_duration_minutes(t) == 15
    assert observation_duration_minutes(t) == OVH + 15


def test_overhead_is_shorter_in_test_mode(monkeypatch):
    """In TEST_MODE lo script salta le operazioni lente, quindi la preparazione e' corta."""
    assert overhead_minutes() == config.OVERHEAD_MINUTES
    monkeypatch.setattr(config, "TEST_MODE", True)
    assert overhead_minutes() == config.OVERHEAD_MINUTES_TEST


def test_take_frames_rotates_between_filters():
    """Lo split rispetta la rotazione dei filtri: poche pose per filtro a giro."""
    taken, remaining = take_frames({"L": 10, "R": 10}, 6, cycle=3)
    assert taken == {"L": 3, "R": 3}
    assert remaining == {"L": 7, "R": 7}


def test_take_frames_stops_when_nothing_left():
    """Chiedendo piu' pose di quante ne restino, si prende tutto e non avanza nulla."""
    taken, remaining = take_frames({"L": 4}, 100, cycle=3)
    assert taken == {"L": 4}
    assert remaining == {}


# ============================================================ priorita' e visibilita'

def test_ranking_prefers_who_sets_first():
    """Priorita' a chi e' visibile per meno tempo: prima chi tramonta prima."""
    ordinati = rank_by_visibility([target("Deneb", DENEB, {"L": 1}),
                                   target("Antares", ANTARES, {"L": 1})],
                                  DATE, min_altitude=10)
    assert [t["name"] for t in ordinati][0] == "Antares"


def test_ranking_drops_never_visible_targets():
    """Un target che non supera mai la soglia non viene proposto."""
    assert rank_by_visibility([target("Antares", ANTARES, {"L": 1})],
                              DATE, min_altitude=80) == []


# ============================================================ orari fissi

def test_fixed_start_is_when_exposures_begin(night):
    """L'orario chiesto dall'utente e' l'inizio delle POSE: lo slot arretra da solo."""
    chiesto = night[0] + 60 * u.min
    s = build_schedule([target("F", DENEB, {"L": 20}, fixed_start=chiesto.iso)], DATE)
    e = s["scheduled"][0]
    assert abs((e["frames_start"] - chiesto).sec) < 1
    assert abs((e["frames_start"] - e["start"]).sec / 60 - OVH) < 0.01
    assert abs((e["end"] - e["frames_start"]).sec / 60 - 20) < 0.01


def test_fixed_too_early_is_rejected(night):
    """Se la preparazione cadrebbe prima del buio, l'orario non e' realizzabile."""
    s = build_schedule([target("F", DENEB, {"L": 20}, fixed_start=night[0].iso)], DATE)
    assert not s["scheduled"]
    assert "preparazione" in s["conflicts"][0]["reason"]


def test_fixed_is_accepted_right_after_the_overhead(night):
    """Il primo orario possibile e' inizio del buio piu' la preparazione."""
    s = build_schedule([target("F", DENEB, {"L": 20},
                               fixed_start=(night[0] + OVH * u.min).iso)], DATE)
    assert len(s["scheduled"]) == 1
    assert abs((s["scheduled"][0]["start"] - night[0]).sec) < 1


def test_fixed_below_horizon_is_impossible():
    """La fisica vince sull'utente: sotto l'orizzonte non si osserva."""
    s = build_schedule([target("A", ANTARES, {"L": 10},
                               fixed_start="2026-08-02 01:00:00")], DATE)
    assert not s["scheduled"]
    assert "orizzonte" in s["conflicts"][0]["reason"]


def test_two_overlapping_fixed_times_are_fifo(night):
    """Due orari fissi sovrapposti: passa chi e' arrivato prima."""
    quando = (night[0] + 60 * u.min).iso
    s = build_schedule([target("Primo", DENEB, {"L": 30}, fixed_start=quando),
                        target("Secondo", VEGA, {"L": 30}, fixed_start=quando)], DATE)
    assert [e["name"] for e in s["scheduled"]] == ["Primo"]
    assert s["conflicts"][0]["name"] == "Secondo"
    assert "FIFO" in s["conflicts"][0]["reason"]


def test_rigid_fixed_that_overruns_says_how_many_would_fit():
    """Un fisso non splittabile che sfora viene rifiutato, dicendo quanto ci starebbe."""
    s = build_schedule([target("R", DENEB, {"L": 200},
                               fixed_start="2026-08-02 01:00:00")], DATE)
    assert not s["scheduled"]
    motivo = s["conflicts"][0]["reason"]
    assert "ci starebbero solo" in motivo and "delle 200" in motivo


def test_fixed_is_truncated_when_the_target_sets(night):
    """Un fisso splittabile su un target che tramonta si ferma al tramonto, non a fine notte."""
    s = build_schedule([target("A", ANTARES, {"L": 300}, splittable=True,
                               fixed_start=(night[0] + 20 * u.min).iso)], DATE)
    assert s["scheduled"][0]["end"] < night[1]


# ============================================================ fisso splittabile multi-notte

def test_splittable_fixed_keeps_the_same_hour_every_night():
    """Un fisso spezzato riparte sempre all'ora impostata, notte dopo notte."""
    righe = slots(plan_campaign([target("M", DENEB, {"L": 200}, splittable=True,
                                        fixed_start="2026-08-02 01:00:00")],
                                DATE, nights=6))
    assert len(righe) > 1
    assert {r[1] for r in righe} == {"01:00"}
    assert sum(r[2] for r in righe) == 200
    assert [r[3] for r in righe] == [True] * (len(righe) - 1) + [False]


def test_booking_date_is_a_floor_not_an_exact_day():
    """La data della richiesta dice 'non prima di', non 'esattamente quel giorno'."""
    righe = slots(plan_campaign([target("P", DENEB, {"L": 30}, splittable=True,
                                        fixed_start="2026-08-03 23:00:00")],
                                DATE, nights=6))
    assert [r[0] for r in righe] == ["2026-08-03"]


def test_past_booking_is_reanchored_to_the_first_useful_night():
    """E' il caso della ripianificazione serale: la data e' passata, l'ora vale ancora."""
    righe = slots(plan_campaign([target("C", DENEB, {"L": 40}, splittable=True,
                                        fixed_start="2026-08-02 01:00:00")],
                                Time("2026-08-03"), nights=3))
    assert len(righe) == 1 and righe[0][1] == "01:00"


def test_continuation_keeps_priority_over_a_newer_fixed_request():
    """FIFO: chi era arrivato prima tiene il posto anche nelle notti successive."""
    piano = plan_campaign([
        target("Prima", DENEB, {"L": 200}, splittable=True,
               fixed_start="2026-08-02 01:00:00"),
        target("Dopo", DENEB, {"L": 20}, fixed_start="2026-08-03 01:00:00"),
    ], DATE, nights=6)
    notte2 = next(n for n in piano["by_night"] if n["date"].iso[:10] == "2026-08-02")
    assert "Prima" in [e["name"] for e in notte2["schedule"]["scheduled"]]
    assert "Dopo" in [c["name"] for c in notte2["schedule"]["conflicts"]]


# ============================================================ target liberi

def test_free_targets_do_not_overlap():
    """Un telescopio solo: due osservazioni non possono sovrapporsi."""
    s = build_schedule([target("A", VEGA, {"L": 30}), target("B", DENEB, {"L": 30})], DATE)
    assert len(s["scheduled"]) == 2
    primo, secondo = sorted(s["scheduled"], key=lambda e: e["start"].jd)
    assert secondo["start"] >= primo["end"]


def test_free_exposures_start_after_the_preparation():
    """Anche i liberi pagano la preparazione prima di cominciare a scattare."""
    e = build_schedule([target("A", VEGA, {"L": 30})], DATE)["scheduled"][0]
    assert abs((e["frames_start"] - e["start"]).sec / 60 - OVH) < 0.01


def test_split_reserves_the_overhead_out_of_the_gap(night):
    """Nel buco disponibile ci stanno le pose SOLO dopo aver tolto la preparazione."""
    vis = rank_by_visibility([target("V", VEGA, {"L": 1})], DATE)[0]
    finestra = (vis["window_end"] - vis["window_start"]).sec / 60
    e = build_schedule([target("V", VEGA, {"L": 5000}, splittable=True)], DATE)["scheduled"][0]
    assert total_frames(e["frames"]) == int(finestra - OVH)


def test_gap_smaller_than_the_overhead_gets_no_split(night):
    """Se il buco non basta nemmeno per la preparazione, non si piazza niente."""
    riempitivo = int((night[1] - night[0]).sec / 60 - OVH - 5)
    s = build_schedule([
        target("Riempitivo", DENEB, {"L": riempitivo},
               fixed_start=(night[0] + OVH * u.min).iso),
        target("Briciola", VEGA, {"L": 100}, splittable=True),
    ], DATE)
    assert "Briciola" not in [e["name"] for e in s["scheduled"]]
    assert any(x["name"] == "Briciola" for x in s["unplaced"])


def test_moon_constraint_can_postpone_a_target():
    """Con la Luna piena e vicina, il target viene rimandato ad altra notte."""
    vicino_alla_luna = {"ra": 22.4, "dec": -7.0}     # vicino alla Luna del 2026-08-01
    s = build_schedule([target("L", vicino_alla_luna, {"L": 10},
                               moon_check=True, moon_base_angle=180,
                               min_altitude=10)], DATE)
    assert not s["scheduled"]
    assert "Luna" in s["unplaced"][0]["reason"]


def test_soft_minimum_altitude_applies_only_to_free_targets(night):
    """L'altezza minima e' una comodita': un orario fisso la scavalca."""
    basso = target("A", ANTARES, {"L": 10}, min_altitude=80)
    assert build_schedule([basso], DATE)["scheduled"] == []
    fisso = target("A", ANTARES, {"L": 10},
                   fixed_start=(night[0] + 20 * u.min).iso)
    assert len(build_schedule([fisso], DATE)["scheduled"]) == 1


# ============================================================ campagna multi-notte

def test_multi_night_split_loses_no_exposures():
    """Su piu' notti non si perde ne' si inventa nemmeno una posa."""
    piano = plan_campaign([target("M", VEGA, {"L": 600}, splittable=True)],
                          DATE, nights=5)
    fatte = sum(r[2] for r in slots(piano))
    rimaste = sum(total_frames(t["frames"]) for t in piano["free_unscheduled"])
    assert fatte + rimaste == 600


def test_every_night_pays_its_own_preparation():
    """Riprendere un'osservazione spezzata costa preparazione ogni notte."""
    piano = plan_campaign([target("M", VEGA, {"L": 600}, splittable=True)],
                          DATE, nights=5)
    for notte in piano["by_night"]:
        for e in notte["schedule"]["scheduled"]:
            assert abs((e["frames_start"] - e["start"]).sec / 60 - OVH) < 0.01


def test_campaign_with_nothing_to_do_is_empty():
    """Nessun target: piano vuoto, senza errori."""
    piano = plan_campaign([], DATE, nights=3)
    assert slots(piano) == []


# ============================================================ helper

def test_anchor_to_night_maps_a_clock_time_into_a_night(night):
    """Le ore dopo mezzanotte appartengono al giorno seguente."""
    assert anchor_to_night("2026-08-01 23:00:00", night).iso[:16] == "2026-08-01 23:00"
    assert anchor_to_night("2026-08-01 01:00:00", night).iso[:16] == "2026-08-02 01:00"
    assert anchor_to_night("2026-08-01 12:00:00", night) is None


def test_above_horizon_until_reports_when_the_target_sets(night):
    """Dice fino a che istante il target resta osservabile, non solo si'/no."""
    fine = above_horizon_until(ANTARES["ra"], ANTARES["dec"], night[0], night[1])
    assert night[0] < fine < night[1]
    sempre = above_horizon_until(DENEB["ra"], DENEB["dec"], night[0], night[1])
    assert sempre == night[1]
