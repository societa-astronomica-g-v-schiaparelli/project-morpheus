# --- Connessione INDIGO e hardware ---
INDIGO_WS_URL = "ws://morpheus.astrogeo.va.it:7624"
HARDWARE_PRESET = "DEFAULT"   # nome del preset INDIGO (load_config)
COOLING_TEMP = -20        # °C, temperatura di raffreddamento della camera
FOCUS_EXP = 5             # s, esposizione per messa a fuoco / precise goto
DOME_WAIT = 120           # s, attesa per l'allineamento della cupola

# --- Scheduling ---
DEFAULT_MIN_ALTITUDE = 30    # gradi, soglia soft di comodita' (solo target liberi)
HORIZON_LIMIT = 0            # gradi, limite fisico dell'orizzonte (anche per i fissi)
NIGHTS_HORIZON = 7          # su quante notti pianificare
WAKE_BEFORE_MIN = 30       # min, quanto prima dell'inizio notte si sveglia morpheus
OVERHEAD_MINUTES = 10      # min, overhead per slot PRIMA delle pose (slew/fuoco/guida)
OVERHEAD_MINUTES_TEST = 1  # min, overhead usato quando TEST_MODE e' attivo (la preparazione e' piu' corta)
FRAMES_PER_CYCLE = 3       # pose per filtro prima di ruotare (rotazione filtri)

# --- Feedback / rete ---
WS_RECV_TIMEOUT = 5             # s, attesa massima per un singolo messaggio da INDIGO
# La libreria websockets manda un "ping" periodico e CHIUDE la connessione se non
# riceve risposta. INDIGO non risponde a quei ping: durante una sequenza lunga la
# connessione cadrebbe sempre (visto dal vivo il 2026-08-01). None = niente ping;
# a proteggerci resta il timeout di await_sequence, che trasforma il silenzio in 'timeout'.
WS_PING_INTERVAL = None
SEQUENCE_TIMEOUT_MARGIN = 300   # s, margine sul timeout di una sequenza (oltre la durata prevista)
LIVE_MIN_INTERVAL = 1.0         # s, ogni quanto al massimo morpheus riscrive lo stato live
LIVE_POLL_INTERVAL = 1.0        # s, ogni quanto l'SSE rilegge lo stato per il browser
LIVE_STALE_AFTER = 120          # s, oltre questo silenzio morpheus e' considerato non attivo

# --- Mappe e liste (fornite dai tecnici) ---
BINNING_TO_MODE = {
    "BIN1X1": "RAW 16 4499x3599",
    "BIN2X2": "RAW 16 2249x1799",
    "BIN4X4": "RAW 16 1124x899",
}
FILTER_LIST = ["L", "R", "G", "B", "R-Photometric", "Ha", "SII", "OIII", "Clear"]
FRAME_TYPE_LIST = ["Light", "Dark", "Bias", "Flat"]
DEFAULT_FRAME_TYPE = "Light"   # di base forziamo sempre Light (tipologia di scatto)

# --- Modalita' di test (SOLO per i test, mai in produzione) ---
TEST_MODE = True          # se True, bypassa focus/guida/precise_goto (simulatore lento)
SIMULATION_MODE = False      # se True, la "notte" e' adesso -> adesso + SIMULATION_MINUTES
SIMULATION_MINUTES = 30      # durata della notte simulata
SIMULATION_GAP_MINUTES = 5   # pausa fra una notte simulata e la successiva (il "giorno")
