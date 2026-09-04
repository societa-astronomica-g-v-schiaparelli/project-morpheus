```
    ███╗   ███╗ ██████╗ ██████╗ ██████╗ ██╗  ██╗███████╗██╗   ██╗███████╗
    ████╗ ████║██╔═══██╗██╔══██╗██╔══██╗██║  ██║██╔════╝██║   ██║██╔════╝
    ██╔████╔██║██║   ██║██████╔╝██████╔╝███████║█████╗  ██║   ██║███████╗
    ██║╚██╔╝██║██║   ██║██╔══██╗██╔═══╝ ██╔══██║██╔══╝  ██║   ██║╚════██║
    ██║ ╚═╝ ██║╚██████╔╝██║  ██║██║     ██║  ██║███████╗╚██████╔╝███████║
    ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚══════╝
             automazione delle notti osservative  ·  ASTRA / Morpheus
```

> **Morpheus** e' il sistema che pianifica ed esegue in autonomia le notti
> osservative dell'osservatorio G.V. Schiaparelli (Varese). Riceve le richieste di
> ripresa, costruisce il piano della serata rispettando i vincoli astronomici e
> meteo, e quando arriva la notte comanda la strumentazione attraverso INDIGO,
> mostrando in tempo reale cosa sta facendo.

Il progetto nasce come lavoro di tesi Bachelor SUPSI-DTI. Nome ufficiale del sistema:
**ASTRA** (Automated Scheduling and Telescope Remote Automation); internamente lo
chiamiamo **Morpheus**, perche' veglia mentre l'osservatorio "dorme".

---

## Cosa fa

- **Raccoglie le richieste** di osservazione (target, coordinate, filtri, pose, esposizione, vincoli).
- **Pianifica la campagna** su piu' notti: visibilita', altezza minima, distanza dalla Luna, orari fissi, e spezzamento delle riprese lunghe su piu' notti.
- **Esegue la nottata** da sola: controlla il meteo, accende la strumentazione, invia ogni osservazione al suo orario, poi mette in sicurezza e spegne.
- **Mostra lo stato dal vivo** nel browser (posa per posa) mentre la serata avanza.
- **Tiene lo storico**: cosa e' stato ripreso, in quali notti, com'e' andata.

---

## Architettura

Due programmi separati che **non si chiamano mai a vicenda**: si coordinano solo
attraverso un database SQLite condiviso. Questo li rende indipendenti (l'app puo'
riavviarsi senza fermare una nottata, e viceversa).

```
   ┌─────────────┐        HTTP / SSE        ┌──────────────────┐
   │   Browser   │ ───────────────────────▶ │   App web        │
   │  (frontend) │ ◀─────────────────────── │   (FastAPI)      │
   └─────────────┘                          └────────┬─────────┘
                                                      │  legge / scrive
                                                      ▼
                                            ┌───────────────────┐
                                            │  morpheus.db      │
                                            │  (SQLite, WAL)    │
                                            └────────┬──────────┘
                                                      ▲  legge / scrive
                                                      │
                                            ┌─────────┴─────────┐      JSON / WebSocket
                                            │   morpheus.py     │ ───────────────────────▶  INDIGO
                                            │  (orchestratore)  │        (Scripting Agent)   (simulatori o telescopio)
                                            └───────────────────┘
```

- **App web** (`backend/main.py`, avviata con `uvicorn`): API REST per creare le
  osservazioni e pianificare, piu' l'interfaccia e il flusso live (SSE).
- **Orchestratore** (`backend/morpheus.py`): il "direttore della nottata". Gira
  sempre, dorme di giorno, si sveglia poco prima della notte ed esegue il piano.
- **INDIGO**: il framework che parla con la strumentazione. Morpheus gli invia gli
  script via WebSocket (lo Scripting Agent).

---

## Requisiti

- **Python 3.12+**
- **[uv](https://github.com/astral-sh/uv)** per l'ambiente virtuale e le dipendenze (non si usa `pip` a mano)
- Accesso a un endpoint **INDIGO** (i simulatori sulla VM `morpheus`, oppure il telescopio vero `babele`)

---

## Installazione

```bash
git clone <url-del-repo> project-morpheus
cd project-morpheus
uv venv                              # crea .venv/
uv pip install -r requirements.txt   # installa le dipendenze bloccate
```

> `requirements.txt` e' una "fotografia" completa con le versioni bloccate. Dopo aver
> aggiunto una libreria (`uv pip install <pkg>`), rigenerala con
> `uv pip freeze > requirements.txt`.

---

## Configurazione

Tutte le manopole stanno in **`backend/config.py`**. Gli interruttori che contano di piu':

| Impostazione        | Valore in esercizio | A cosa serve                                                              |
| ------------------- | ------------------- | ------------------------------------------------------------------------ |
| `USE_SIMULATOR`     | `True`              | `True` = simulatori INDIGO sulla VM; `False` = **telescopio vero (babele)** |
| `TEST_MODE`         | `False`             | Se `True`, salta fuoco / guida / precise goto (utile solo per prove veloci) |
| `SIMULATION_MODE`   | `False`             | Se `True`, usa notti "finte" compresse invece di quelle astronomiche vere   |
| `WS_RECV_TIMEOUT`   | `120`               | Attesa massima per un messaggio da INDIGO (evita cadute su sequenze lunghe)  |

Un solo interruttore, `USE_SIMULATOR`, decide **indirizzo, preset e modalita' della
camera** in blocco: cambiare quei valori a mano uno per uno e' il modo piu' facile
per puntare al telescopio vero per sbaglio.

> ⚠️ **Hardware reale (`babele`).** Impostare `USE_SIMULATOR = False` fa comandare il
> **telescopio vero** in osservatorio. Farlo **solo insieme ai tecnici**. In tutti gli
> altri casi lasciare `USE_SIMULATOR = True`: si lavora sui simulatori INDIGO, che si
> comportano come la strumentazione reale senza alcun rischio.

---

## Avvio

Servono **due processi** (in due terminali). Entrambi partono dalla cartella `backend/`.

**1) App web + interfaccia**

```bash
cd backend
../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 1
```

> L'opzione `--timeout-graceful-shutdown 1` **serve**: con una pagina aperta, il flusso
> live (SSE) tiene la connessione viva e senza di essa `uvicorn` non si chiude con Ctrl+C.

Interfaccia web: **http://<host>:8000/ui/**

**2) Orchestratore della nottata**

```bash
cd backend
../.venv/bin/python morpheus.py
```

In esercizio normale morpheus dorme finche' non si avvicina la prossima notte
astronomica, poi esegue il piano da solo. Per una prova immediata su una notte
"finta", accendere `SIMULATION_MODE` (ed eventualmente `TEST_MODE`) in `config.py`.

---

## Interfaccia web

Dalla pagina `/ui/` si puo':

- **inserire un'osservazione** (coordinate accettate sia in sessagesimale `18:00:00` sia in decimale `0.782`);
- **generare e vedere il piano** della campagna, notte per notte;
- **seguire dal vivo** cosa sta facendo morpheus (fase, target, avanzamento delle pose);
- **consultare lo storico** delle osservazioni completate.

---

## API

Base comune: **`/api/v1`**.

| Metodo | Endpoint                      | Descrizione                                                        |
| ------ | ----------------------------- | ----------------------------------------------------------------- |
| `POST` | `/observations`               | Crea una richiesta di osservazione                                |
| `GET`  | `/observations`               | Elenco delle osservazioni ancora da fare                          |
| `GET`  | `/history`                    | Storico delle completate (con il dettaglio delle notti)           |
| `GET`  | `/executions?night=<data>`    | Diario di bordo: cosa e' stato eseguito e com'e' andato           |
| `GET`  | `/config`                     | Liste per i menu del frontend (binning, filtri, tipi di posa)     |
| `POST` | `/dispatch/{id}`              | Collaudo manuale: lancia UNA osservazione adesso                  |
| `POST` | `/schedule?date=<data>`       | Pianifica la campagna a partire dalla data indicata               |
| `GET`  | `/weather?date=<data>`        | Verdetto meteo per una notte                                      |
| `POST` | `/night/start?date=<data>`    | Inizio nottata: ripianifica (o annulla se il meteo e' avverso)    |
| `GET`  | `/live`                       | Stato corrente di morpheus, una volta sola                        |
| `GET`  | `/live/stream`                | Stesso stato in flusso continuo (SSE)                             |

Documentazione interattiva generata da FastAPI: **http://<host>:8000/docs**

---

## Test

```bash
.venv/bin/python -m pytest tests/ -q
```

I test girano su un database usa-e-getta con un WebSocket finto: non serve alcun
telescopio (ne' simulatore) acceso.

---

## Struttura del progetto

```
project-morpheus/
├── backend/
│   ├── main.py                 # app FastAPI (API + interfaccia + SSE)
│   ├── morpheus.py             # orchestratore della nottata
│   ├── config.py               # tutte le impostazioni (simulatore/hardware, scheduling, ...)
│   ├── db.py                   # accesso al database (SQLite/SQLModel)
│   ├── api/                    # router REST (osservazioni, scheduler, live)
│   ├── services/               # scheduler, astronomia, meteo, generatore di script, dispatcher INDIGO
│   ├── utils/                  # utilita' (parsing coordinate, ...)
│   └── vendor/                 # Sequencer.js (il "preludio" INDIGO)
├── frontend/                   # interfaccia statica (HTML + Pico CSS)
├── tests/                      # test automatici
└── requirements.txt
```

---

## Licenza

Vedi il file [LICENSE](LICENSE).
