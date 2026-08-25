import textwrap
import config

class IndigoScriptGenerator:
    """
    Generatore di script ECMAScript per il controllo di INDIGO Astronomy.
    La struttura ricalca l'esempio commentato fornito dai tecnici (validato a mano
    nell'Ain Imager): "first config" una volta a inizio nottata, poi un blocco per
    ogni oggetto, infine lo spegnimento.
    """
    COOLING_TEMP = config.COOLING_TEMP
    FOCUS_EXP = config.FOCUS_EXP
    GUIDE_EXP = config.GUIDE_EXP
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
        """La "first config" dell'esempio dei tecnici: carica il preset (connette e
        seleziona i dispositivi), abilita le funzioni avanzate del telescopio, avvia il
        raffreddamento e fissa una volta per tutte tipo di scatto e formato immagine.
        Il flip al meridiano e il formato immagine dipendono dall'hardware, quindi si
        emettono solo se il config attivo li prevede (sul simulatore no)."""
        lines = [f'sequence.load_config("{config.HARDWARE_PRESET}");']
        if config.ENABLE_MERIDIAN_FLIP:
            lines.append("sequence.enable_meridian_flip(true, 0);")
        lines.append(f'sequence.select_frame_type("{config.DEFAULT_FRAME_TYPE}");')
        if config.IMAGE_FORMAT:
            lines.append(f'sequence.select_image_format("{config.IMAGE_FORMAT}");')
        lines.append(f"sequence.enable_cooler({self.COOLING_TEMP});")
        return "\n".join(lines)

    def generate_observation(self, target_name: str, ra: float, dec: float, frames: dict,
                             exposition: float, binning: str, guide: bool = False,
                             focus: bool = False, sequential: bool = False,
                             wait_until: str | None = None) -> str:
        """
        Il blocco di UN oggetto, nell'ordine dell'esempio dei tecnici:
        nome -> slew -> attesa cupola -> modalita' camera -> (fuoco/precise goto/guida)
        -> [wait_until] -> pose -> stop guida.

        Le coordinate vanno passate come NUMERI (ra in ore, dec in gradi), come nello
        script di riferimento: sequence.slew(9.9313, 69.6794).

        'wait_until' (ISO UTC), se presente, inchioda l'inizio delle POSE a quell'istante:
        la preparazione (overhead) avviene prima, poi il telescopio aspetta l'ora esatta.
        Serve agli orari fissi; per gli altri oggetti resta None (si parte appena pronti).

        In TEST_MODE si saltano fuoco, precise_goto, guida e wait_until: sul simulatore
        sono lenti o non rilevanti e allungherebbero solo i collaudi.
        """
        camera_mode = config.BINNING_TO_MODE[binning]   # es. "BIN2X2" -> "RAW 16 2249x1799"
        script = textwrap.dedent(f"""
                sequence.set_object_name("{target_name}");
                sequence.slew({ra}, {dec});
                sequence.wait({self.DOME_WAIT});
                sequence.select_camera_mode("{camera_mode}");
            """)

        # preparazione lenta: fuoco, puntamento fine e guida. Il fuoco e' legato al
        # filtro, quindi si seleziona il primo filtro utile prima di metterlo a fuoco.
        if not config.TEST_MODE:
            first_filter = next((f for f, c in frames.items() if c > 0), None)
            if first_filter is not None:
                script += f'sequence.select_filter("{first_filter}");\n'
            if focus:
                script += f"sequence.focus_ignore_failure({self.FOCUS_EXP});\n"
            script += f"sequence.precise_goto({self.FOCUS_EXP}, {ra}, {dec});\n"
            if guide:
                script += f"sequence.start_guiding({self.GUIDE_EXP});\n"
            if wait_until:
                script += f'sequence.wait_until("{wait_until}");\n'

        script += self._build_capture_sequence(frames, exposition, sequential)

        if not config.TEST_MODE and guide:
            script += "sequence.stop_guiding();\n"

        return script

    def generate_shutdown(self) -> str:
        """Spegne tutto a fine nottata: mette in parcheggio e spegne il raffreddamento.
        NB l'esempio dei tecnici usa load_config("Empty") al posto di disable_cooler;
        da confermare con loro che "Empty" esista anche su babele (sul simulatore no)."""
        return textwrap.dedent("""
            sequence.park();
            sequence.disable_cooler();
        """)

    def finalize_script(self, script_body: str) -> str:
        """
        'Timbra' lo script aggiungendo l'istanza dell'oggetto Sequence all'inizio
        e il comando di avvio alla fine. Da chiamare solo prima dell'invio a INDIGO.
        """
        final_script = "var sequence = new Sequence();\n"
        final_script += script_body
        final_script += "\nsequence.start();\n"

        return final_script
