"""
Test del generatore di script INDIGO.

Bloccano la struttura decisa con i tecnici (esempio commentato dello script reale):
nomi delle funzioni, ordine dei comandi, coordinate come NUMERI, wait_until per gli
orari fissi, e i comandi lenti saltati in TEST_MODE. Non serve INDIGO: si controlla
solo il TESTO generato.
"""
import config
from services.script_generator import IndigoScriptGenerator

gen = IndigoScriptGenerator()


def test_startup_has_the_first_config_basics():
    script = gen.generate_startup()
    assert f'sequence.load_config("{config.HARDWARE_PRESET}");' in script
    assert f"sequence.enable_cooler({config.COOLING_TEMP});" in script
    # tipo di scatto fissato UNA volta nello startup (non piu' per-oggetto)
    assert f'sequence.select_frame_type("{config.DEFAULT_FRAME_TYPE}");' in script


def test_startup_meridian_flip_and_image_format_follow_config():
    """Flip al meridiano e formato immagine dipendono dall'hardware: si emettono solo
    se il config attivo li prevede (sul simulatore no)."""
    script = gen.generate_startup()
    assert ("enable_meridian_flip" in script) == config.ENABLE_MERIDIAN_FLIP
    assert ("select_image_format" in script) == bool(config.IMAGE_FORMAT)


def test_observation_uses_numeric_coordinates():
    """slew/precise_goto vogliono numeri (ra ore, dec gradi), non stringhe fra virgolette."""
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 3}, 60,
                                      "BIN1X1", guide=True, focus=True)
    assert "sequence.slew(9.9313, 69.6794);" in script
    assert 'sequence.slew("9.9313"' not in script


def test_observation_full_order_when_not_in_test_mode(monkeypatch):
    monkeypatch.setattr(config, "TEST_MODE", False)
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 3}, 60,
                                      "BIN1X1", guide=True, focus=True)
    for cmd in ["set_object_name", "slew", "wait(", "select_camera_mode",
                "focus_ignore_failure", "precise_goto", "start_guiding",
                "capture_batch", "stop_guiding"]:
        assert cmd in script, f"manca {cmd}"
    # il fuoco (legato al filtro) viene dopo aver selezionato un filtro
    assert script.index("select_filter") < script.index("focus_ignore_failure")


def test_test_mode_skips_the_slow_commands(monkeypatch):
    monkeypatch.setattr(config, "TEST_MODE", True)
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 3}, 60,
                                      "BIN1X1", guide=True, focus=True, wait_until="2026-07-29 22:30:00")
    for cmd in ["focus_ignore_failure", "precise_goto", "start_guiding",
                "stop_guiding", "wait_until"]:
        assert cmd not in script, f"{cmd} non doveva esserci in TEST_MODE"
    # le pose invece ci sono sempre
    assert "capture_batch" in script


def test_wait_until_pins_the_start_for_fixed_slots(monkeypatch):
    monkeypatch.setattr(config, "TEST_MODE", False)
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 3}, 60,
                                      "BIN1X1", guide=True, focus=True,
                                      wait_until="2026-07-29 22:30:00")
    # wait_until sta DOPO la preparazione e PRIMA delle pose
    assert 'sequence.wait_until("2026-07-29 22:30:00");' in script
    assert script.index("start_guiding") < script.index("wait_until") < script.index("capture_batch")


def test_capture_rotation_alternates_filters(monkeypatch):
    monkeypatch.setattr(config, "TEST_MODE", True)   # solo le pose, piu' facile da leggere
    monkeypatch.setattr(config, "FRAMES_PER_CYCLE", 3)
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 6, "R": 6}, 60,
                                      "BIN1X1", sequential=False)
    # rotazione: L(3) R(3) L(3) R(3)
    filtri = [line.split('"')[1] for line in script.splitlines() if "select_filter" in line]
    assert filtri == ["L", "R", "L", "R"]


def test_capture_sequential_finishes_a_filter_before_the_next(monkeypatch):
    monkeypatch.setattr(config, "TEST_MODE", True)
    script = gen.generate_observation("M82", 9.9313, 69.6794, {"L": 6, "R": 6}, 60,
                                      "BIN1X1", sequential=True)
    filtri = [line.split('"')[1] for line in script.splitlines() if "select_filter" in line]
    assert filtri == ["L", "R"]   # un batch pieno per filtro
