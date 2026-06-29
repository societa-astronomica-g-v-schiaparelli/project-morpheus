STANDARD_COOLING_TEMPERATURE = -20
DOME_ALIGNMENT_WAIT_SECONDS = 120

def generate_observation_script(name: str, ra: str, dec: str, filter: str, frames: int, exposure: float) -> str:
    script = (
        "var sequence = new Sequence()\n"
        f"sequence.set_object_name(\"{name}\")\n"
        f"sequence.enable_cooler({STANDARD_COOLING_TEMPERATURE})\n"
        f"sequence.slew({ra},{dec})\n"
        f"sequence.wait({DOME_ALIGNMENT_WAIT_SECONDS})\n"
        f"sequence.select_filter(\"{filter}\")\n"
        f"sequence.capture_batch({frames},{exposure})\n"
    )
    return script

if __name__ == "__main__":
    print(generate_observation_script(
        name="M31",
        ra="00h42m44s",
        dec="+41d16m09s",
        filter="L",
        frames=10,
        exposure=30.0
    ))