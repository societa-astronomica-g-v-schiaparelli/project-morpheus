import textwrap
import config

class IndigoScriptGenerator:
    """
    Generatore di script ECMAScript per il controllo di INDIGO Astronomy.
    """
    COOLING_TEMP = config.COOLING_TEMP
    FOCUS_EXP = config.FOCUS_EXP
    DOME_WAIT = config.DOME_WAIT

    def _build_capture_sequence(self, frames: dict, exposition: float, sequential: bool) -> str:
        """Helper: genera gli scatti dato 'frames' = {filtro: n_pose}.
        Sequenziale: tutte le pose di un filtro, poi il prossimo filtro.
        Altrimenti: rotazione (FRAMES_PER_CYCLE pose per filtro a giro, finche' finite)."""
        script_chunk = ""

        if sequential:
            for f, count in frames.items():
                if count > 0:
                    script_chunk += f'sequence.select_filter("{f}");\n'
                    script_chunk += f"sequence.capture_batch({count},{exposition});\n"
            return script_chunk

        cycle = config.FRAMES_PER_CYCLE
        remaining = {f: c for f, c in frames.items() if c > 0}
        while remaining:
            for f in list(remaining.keys()):
                take = min(cycle, remaining[f])
                script_chunk += f'sequence.select_filter("{f}");\n'
                script_chunk += f"sequence.capture_batch({take},{exposition});\n"
                remaining[f] -= take
                if remaining[f] <= 0:
                    del remaining[f]
        return script_chunk

    def generate_startup(self) -> str:
        """Carica il preset hardware (connette e seleziona i dispositivi) e accende
        i sistemi a inizio nottata. Il preset sostituisce la selezione manuale."""
        return (f'sequence.load_config("{config.HARDWARE_PRESET}");\n'
                f"sequence.enable_cooler({self.COOLING_TEMP});")

    def generate_observation(self, target_name: str, ra: str, dec: str, frames: dict, exposition: float, binning: str, guide: bool = False, focus: bool = False, sequential: bool = False) -> str:
        """
        Muove il telescopio e gestisce tutti i parametri di osservazione.
        """
        camera_mode = config.BINNING_TO_MODE[binning]   # es. "BIN2X2" -> "RAW 16 2249x1799"
        script = textwrap.dedent(f"""
                sequence.set_object_name("{target_name}");
                sequence.select_camera_mode("{camera_mode}");
                sequence.select_frame_type("{config.DEFAULT_FRAME_TYPE}");
                sequence.slew("{ra}", "{dec}");
                sequence.wait({self.DOME_WAIT});
            """)

        # precise_goto, messa a fuoco e guida sono lente sul simulatore:
        # in TEST_MODE le saltiamo per velocizzare i collaudi.
        if not config.TEST_MODE:
            script += f"sequence.precise_goto({self.FOCUS_EXP},{ra},{dec});\n"
            if focus:
                script += f"sequence.focus({self.FOCUS_EXP});\n"
            if guide:
                script += f"sequence.start_guiding({self.FOCUS_EXP});\n"

        script += self._build_capture_sequence(frames, exposition, sequential)

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