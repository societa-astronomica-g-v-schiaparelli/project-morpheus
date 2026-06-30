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

    def generate_observation(self, target_name: str, ra: str, dec: str) -> str:
        """Muove il telescopio, aspetta la cupola e avvia la guida."""
        return f"""
            sequence.slew("{ra}", "{dec}");
            sequence.wait({self.DOME_WAIT});
            sequence.start_guiding({self.FOCUS_EXP});
        """

    def generate_standby(self) -> str:
        """Mette in sicurezza il telescopio durante i buchi temporali."""
        return "sequence.park();"

    def generate_shutdown(self) -> str:
        """Spegne tutto a fine nottata."""
        return """
            sequence.park();
            sequence.disable_cooler();
        """

if __name__ == "__main__":
    generator = IndigoScriptGenerator()
    
    print("--- TEST OSSERVAZIONE ---")
    script = generator.generate_observation("Orione", "05h 35m", "-05d 23m")
    print(script)