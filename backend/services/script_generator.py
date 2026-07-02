import textwrap

class IndigoScriptGenerator:
    """
    Generatore di script ECMAScript per il controllo di INDIGO Astronomy.
    """
    COOLING_TEMP = -20  # ° C
    FOCUS_EXP = 5       # Seconds    
    DOME_WAIT = 120     # Seconds

    def _build_capture_sequence(self, frames: int, exposition: float, filters: list[str], sequential: bool) -> str:
        """Helper: Genera la porzione di script JS dedicata agli scatti e ai filtri."""
        script_chunk = ""

        if sequential:
            for f in filters:
                script_chunk += f"sequence.select_filter(\"{f}\");\n"
                script_chunk += f"sequence.capture_batch({frames},{exposition});\n"
            return script_chunk
        
        max_cycle_time = 900 # in Seconds
        max_time_per_filter = max_cycle_time / len(filters)

        # Quanti scatti ci stanno dentro?
        frames_per_cycle = int(max_time_per_filter / exposition)

        # Evito divisioni per zero e limiti superati
        if frames_per_cycle < 1:
            frames_per_cycle = 1
        if frames_per_cycle > frames:
            frames_per_cycle = frames

        # Quanti cicli per completare?
        cycles = frames // frames_per_cycle
        
        # Ne manca qualcuno?
        remainder = frames % frames_per_cycle

        if cycles > 0:
            inner = ""
            for f in filters:
                inner += f'    sequence.select_filter("{f}");\n'
                inner += f"    sequence.capture_batch({frames_per_cycle},{exposition});\n"
            script_chunk += f"sequence.repeat({cycles}, function() {{\n{inner}}});\n"

        # Generazione script per gli scatti rimanenti
        if remainder > 0:
            for f in filters:
                script_chunk += f'sequence.select_filter("{f}");\n'
                script_chunk += f"sequence.capture_batch({remainder},{exposition});\n"
            
        return script_chunk

    def generate_startup(self) -> str:
        """Accende i sistemi a inizio nottata."""
        return f"sequence.enable_cooler({self.COOLING_TEMP});"

    def generate_observation(self, target_name: str, ra: str, dec: str, frames: int, exposition: float, filters: list[str], mode: str, guide: bool = False, focus: bool = False, sequential: bool = False) -> str:
        """
        Muove il telescopio e gestisce tutti i parametri di osservazione.
        """
        script = textwrap.dedent(f"""
                sequence.set_object_name("{target_name}");
                sequence.select_camera_mode("{mode}");
                sequence.slew("{ra}", "{dec}");
                sequence.wait({self.DOME_WAIT});
                sequence.precise_goto({self.FOCUS_EXP},{ra},{dec});
            """)

        if focus:
            script += f"sequence.focus({self.FOCUS_EXP});\n"

        if guide:
            script += f"sequence.start_guiding({self.FOCUS_EXP});\n"

        script += self._build_capture_sequence(frames, exposition, filters, sequential)

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

    def finalize_script(self, script_body: str) -> str:
        """
        'Timbra' lo script aggiungendo l'istanza dell'oggetto Sequence all'inizio 
        e il comando di avvio alla fine. Da chiamare solo prima dell'invio a INDIGO.
        """
        final_script = "var sequence = new Sequence();\n"
        final_script += script_body
        final_script += "\nsequence.start();\n"
        
        return final_script