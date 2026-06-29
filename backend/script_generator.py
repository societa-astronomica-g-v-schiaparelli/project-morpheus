STANDARD_COOLING_TEMPERATURE    = -20   # ° C
STANDARD_FOCUSING_EXPOSURE      = 5     # Seconds
STANDARD_FOCUSING_FILTER        = "L"   # Luminance
STANDARD_DOME_ALIGNMENT_WAIT    = 120   # Seconds


def generate_startup_script() -> str:
    # raffreddamento camera, focus
    startup_script = (
        f"sequence.enable_cooler({STANDARD_COOLING_TEMPERATURE});\n"
        f"sequence.select_filter({STAMDARD_FOCUSING_FILTER});\n"
        f"sequence.focus({STANDARD_FOCUSING_EXPOSURE});\n"
    )
    return startup_script

def generate_observation_script(obj_name: str, camera_mode: str, ra: str, dec: str, refocus: bool) -> str:
    # slew, solve & center, guida, filtro, frame, esposizione, nome, scatti
    observation_script = (
        f"sequence.set_object_name({obj_name});\n"
        f"sequence.select_camera_mode({camera_mode});\n"
        f"sequence.slew({ra}, {dec});\n"
        f"sequence.wait({STANDARD_DOME_ALIGNMENT_WAIT});\n"
    )

    if refocus:
        observation_script += (
            f"sequence.select_filter({STAMDARD_FOCUSING_FILTER});\n"
            f"sequence.focus({STANDARD_FOCUSING_EXPOSURE});\n"
        )

    observation_script += (
        f"sequence.precise_goto({STANDARD_FOCUSING_EXPOSURE}, 0, 0);\n"
        f"sequence.wait({STANDARD_DOME_ALIGNMENT_WAIT}/4);\n"
    )

    # Da capire bene come gestire più cicli con filtri differenti
    observation_script += (
        f"sequence.repeat({n},function(){{\n"
        f"\tsequence.select_filter(...)\n;"
        f"\tsequence.capture_batch(...)\n;"
        f"}});\n"
    )
    # ...

    observation_script += "sequence.stop_guiding();"
    return observation_script

def generate_shutdown_script() -> str:
    # parcheggio telescopio, spegnimento raffreddamento
    pass


def _create_sequence() -> str:
    return "var sequence = new Sequence();\n"

if __name__ == "__main__":
    print(_create_sequence())