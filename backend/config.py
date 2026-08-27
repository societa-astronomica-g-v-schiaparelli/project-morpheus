# --- QUALE INDIGO: simulatori o telescopio vero? ---
# True  = simulatori sulla VM (morpheus). E' la modalita' normale per provare.
# False = HARDWARE VERO in osservatorio (babele): da usare SOLO insieme ai tecnici.
# Un unico interruttore decide indirizzo, preset e modalita' della camera, che sono
# diversi fra i due sistemi: cambiarli a mano uno per uno e' un ottimo modo per
# puntare al telescopio vero per sbaglio.
USE_SIMULATOR = True

if USE_SIMULATOR:
    INDIGO_WS_URL = "ws://morpheus.astrogeo.va.it:7624"
    HARDWARE_PRESET = "Default"       # l'unico preset del server dei simulatori
    DOME_WAIT = 5                     # nessuna cupola da girare davvero
    BINNING_TO_MODE = {               # etichette di CCD_MODE del simulatore
        "BIN1X1": "RAW 1600x1200",
        "BIN2X2": "RAW 800x600",
        "BIN4X4": "RAW 400x300",
    }
    ENABLE_MERIDIAN_FLIP = False      # il mount simulato non ha il flip al meridiano
    IMAGE_FORMAT = None               # sul simulatore non forziamo il formato immagine
else:
    INDIGO_WS_URL = "ws://babele.astrogeo.va.it:7624"
    HARDWARE_PRESET = "Default"       # ⚠️ nome ANCORA DA CONFERMARE con i tecnici
    DOME_WAIT = 120                   # s, attesa per l'allineamento della cupola
    BINNING_TO_MODE = {               # etichette di CCD_MODE della camera vera
        "BIN1X1": "RAW 16 4499x3599",
        "BIN2X2": "RAW 16 2249x1799",
        "BIN4X4": "RAW 16 1124x899",
    }
    ENABLE_MERIDIAN_FLIP = True       # funzione avanzata del telescopio vero
    IMAGE_FORMAT = "FITS format"      # formato di salvataggio richiesto dai tecnici

COOLING_TEMP = -20        # °C, temperatura di raffreddamento della camera
FOCUS_EXP = 5             # s, esposizione per messa a fuoco / precise goto
GUIDE_EXP = 2             # s, esposizione della camera di guida (start_guiding)
STARTUP_SETTLE = 15       # s, pausa dopo lo startup prima di mandare la prima osservazione:
                          # load_config impiega qualche secondo e uno script inviato mentre
                          # e' ancora in corso viene rifiutato (SEQUENCE_STATE -> Alert)
PRELUDE_SETTLE = 2        # s, pausa dopo aver caricato il preludio (definizione della classe
                          # Sequence): lasciare a INDIGO il tempo di interpretarlo prima di
                          # mandare il primo script che usa 'new Sequence()'

# --- Scheduling ---
DEFAULT_MIN_ALTITUDE = 30    # gradi, soglia soft di comodita' (solo target liberi)
HORIZON_LIMIT = 0            # gradi, limite fisico dell'orizzonte (anche per i fissi)
NIGHTS_HORIZON = 7          # su quante notti pianificare
WAKE_BEFORE_MIN = 30       # min, quanto prima dell'inizio notte si sveglia morpheus
OVERHEAD_MINUTES = 10      # min, overhead per slot PRIMA delle pose (slew/fuoco/guida)
OVERHEAD_MINUTES_TEST = 0.25  # min, overhead usato quando TEST_MODE e' attivo (la preparazione e' piu' corta)
FRAMES_PER_CYCLE = 3       # pose per filtro prima di ruotare (rotazione filtri)

# --- Feedback / rete ---
WS_RECV_TIMEOUT = 120             # s, attesa massima per un singolo messaggio da INDIGO
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
# NB BINNING_TO_MODE sta piu' in alto: dipende da quale camera si sta usando.
FILTER_LIST = ["L", "R", "G", "B", "R-Photometric", "Ha", "SII", "OIII", "Clear"]
FRAME_TYPE_LIST = ["Light", "Dark", "Bias", "Flat"]
DEFAULT_FRAME_TYPE = "Light"   # di base forziamo sempre Light (tipologia di scatto)

# --- Modalita' di test (SOLO per i test, mai in produzione) ---
TEST_MODE = True          # se True, bypassa focus/guida/precise_goto (simulatore lento)
SIMULATION_MODE = True      # se True, la "notte" e' adesso -> adesso + SIMULATION_MINUTES
SIMULATION_MINUTES = 2      # durata della notte simulata
SIMULATION_GAP_MINUTES = 1   # pausa fra una notte simulata e la successiva (il "giorno")
