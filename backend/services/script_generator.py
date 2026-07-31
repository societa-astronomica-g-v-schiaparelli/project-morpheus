import textwrap
import config

class IndigoScriptGenerator:
    """
    Generatore di script ECMAScript per il controllo di INDIGO Astronomy.
    """
    COOLING_TEMP = config.COOLING_TEMP
    FOCUS_EXP = config.FOCUS_EXP
    DOME_WAIT = config.DOME_WAIT

    def _build_capture_sequence(self, frames: int, exposition: float, filters: list[str], sequential: bool) -> str:
        """Helper: Genera la porzione di script JS dedicata agli scatti e ai filtri."""
        script_chunk = ""

        if sequential:
            for f in filters:
                script_chunk += f"sequence.select_filter(\"{f}\");\n"
                script_chunk += f"sequence.capture_batch({frames},{exposition});\n"
            return script_chunk
        
        if frames < 1:
            return script_chunk
        frames_per_cycle = min(config.FRAMES_PER_CYCLE, frames)
        cycles = frames // frames_per_cycle
        remainder = frames % frames_per_cycle

        if cycles > 0:
            inner = ""
            for f in filters:
                inner += f'    sequence.select_filter("{f}");\n'
                inner += f"    sequence.capture_batch({frames_per_cycle},{exposition});\n"
            script_chunk += f"sequence.repeat({cycles}, function() {{\n{inner}}});\n"

        # pose rimanenti (se 'frames' non e' multiplo di FRAMES_PER_CYCLE)
        if remainder > 0:
            for f in filters:
                script_chunk += f'sequence.select_filter("{f}");\n'
                script_chunk += f"sequence.capture_batch({remainder},{exposition});\n"

        return script_chunk

    def generate_startup(self) -> str:
        """Carica il preset hardware (connette e seleziona i dispositivi) e accende
        i sistemi a inizio nottata. Il preset sostituisce la selezione manuale."""
        return (f'sequence.load_config("{config.HARDWARE_PRESET}");\n'
                f"sequence.enable_cooler({self.COOLING_TEMP});")

    def generate_observation(self, target_name: str, ra: str, dec: str, frames: int, exposition: float, filters: list[str], binning: str, guide: bool = False, focus: bool = False, sequential: bool = False) -> str:
        """
        Muove il telescopio e gestisce tutti i parametri di osservazione.
        """
        camera_mode = config.BINNING_TO_MODE[binning]   # es. "BIN2X2" -> "RAW 16 2249x1799"
        script = textwrap.dedent(f"""
                sequence.set_object_name("{target_name}");
                sequence.select_camera_mode("{camera_mode}");
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