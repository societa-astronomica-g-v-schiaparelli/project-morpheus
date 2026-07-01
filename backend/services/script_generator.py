import textwrap

class IndigoScriptGenerator:
    """
    Generatore di script ECMAScript per il controllo di INDIGO Astronomy.
    """
    COOLING_TEMP = -20  # ° C
    FOCUS_EXP = 5       # Seconds    
    DOME_WAIT = 120     # Seconds

    def generate_startup(self) -> str:
        """Accende i sistemi a inizio nottata."""
        return f"sequence.enable_cooler({self.COOLING_TEMP});"

    def generate_observation(self, target_name: str, ra: str, dec: str, frames: int, exposition: float, filter: str, mode: str, guide: bool = False, focus: bool = False) -> str:
        """Muove il telescopio e gestisce tutti i parametri di osservazione"""
        script = textwrap.dedent(f"""
                sequence.set_object_name("{target_name}");
                sequence.select_camera_mode("{mode}");
                sequence.select_filter("{filter}");
                sequence.slew("{ra}", "{dec}");
                sequence.wait({self.DOME_WAIT});
                sequence.precise_goto({self.FOCUS_EXP},{ra},{dec});
            """)

        if focus:
            script += f"sequence.focus({self.FOCUS_EXP});\n"

        if guide:
            script += f"sequence.start_guiding({self.FOCUS_EXP});\n"

        script += f"sequence.capture_batch({frames},{exposition});\n"
        
        return script


    def generate_standby(self) -> str:
        """Mette in sicurezza il telescopio durante i buchi temporali."""
        return "sequence.park();"

    def generate_shutdown(self) -> str:
        """Spegne tutto a fine nottata."""
        return """
            sequence.park();
            sequence.disable_cooler();
        """

# if __name__ == "__main__":
#     generator = IndigoScriptGenerator()
    
#     print("--- TEST OSSERVAZIONE ---")
#     script = generator.generate_observation("Orione", "05h 35m", "-05d 23m", 4, 30, "R", "RAW 16 1124x899", True)
#     print(script)