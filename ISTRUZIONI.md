# Video → MIDI — istruzioni per l'installazione

Trasforma la **registrazione dello schermo del piano roll di FL Studio** in un file
`.mid`: le note che vedi disegnate diventano note MIDI vere, con inizio e fine.

> **Se vuoi solo provarlo, senza installare niente:** apri
> [il notebook Colab](../colab/piano_roll_to_midi.ipynb) →
> `Runtime → Esegui tutto` → carica il video → scarica il `.mid`.
> La stessa cosa, in HTML e più breve, è in [docs/guida.html](../docs/guida.html).

Non serve nessun modello da scaricare e nessun peso da mettere in una cartella: non
c'è una rete neurale. È una pipeline di visione artificiale classica (OpenCV + numpy)
che legge la tastiera, la griglia dei semitoni e la testina del playhead, e converte
i pixel in tempo. Tutto quello che serve sono i due file `.py` qui dentro.

---

## 1. Cosa c'è nel progetto

| File | Serve? | Cos'è |
| --- | --- | --- |
| `video2midi.py` | **sì** | il convertitore vero e proprio (riga di comando) |
| `web_video2midi.py` | **sì** | il sito locale con drag & drop, log, player e piano roll |
| `logo-raedius.jpg` | no | solo il logo mostrato nella pagina |
| `requirements.txt` | sì, per installare | le 4 librerie Python necessarie |
| `avvia_midi.command` | comodo su macOS | doppio clic e parte il sito |
| `avvia_midi.bat` | comodo su Windows | doppio clic e parte il sito |
| `ISTRUZIONI.md` | — | questo file |

I due `.py` devono restare **nella stessa cartella**: il sito lancia `video2midi.py`
come sottoprocesso dalla propria directory.

---

## 2. Requisiti

* **Python 3.9 o superiore** (qui è stato provato con 3.14.6). Su macOS:
  `python3 --version`; se manca, installalo da <https://www.python.org/downloads/> o
  con `brew install python`.
* **4 librerie Python**: `opencv-python`, `numpy`, `flask`, `mido` (sono in
  `requirements.txt`).
* **Tesseract OCR — facoltativo ma consigliato.** Serve solo a leggere le etichette
  dei tasti (`C4`, `F#3`…) per capire da che ottava parte la griglia. Se non c'è, il
  programma funziona lo stesso ma va detto a mano con `--ottava` (vedi §5).
  * macOS: `brew install tesseract`
  * Windows: installer da <https://github.com/UB-Mannheim/tesseract/wiki>
  * Debian/Ubuntu: `sudo apt install tesseract-ocr`
* **NON serve ffmpeg**: il video viene letto direttamente da OpenCV.
* Il video deve essere una **registrazione dello schermo** del piano roll (mp4/mov/
  mkv/avi/webm…). Non è un riconoscimento audio: se il piano roll non si vede, non
  c'è niente da leggere.

---

## 3. Installazione (una volta sola)

Apri il Terminale nella cartella dello zip scompattato e:

```bash
# 1) environment isolato (consigliato, non tocca il Python di sistema)
python3 -m venv .venv

# 2) attivalo
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate.bat       # Windows (cmd)

# 3) installa le librerie
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Se non vuoi il virtualenv, basta `python3 -m pip install -r requirements.txt`
(eventualmente con `--user`), ma il virtualenv evita conflitti con altri progetti.

---

## 4. Avviare il sito (è questo il `127.0.0.1:7864`)

```bash
cd piano-roll-to-midi
python3 web_video2midi.py            # → http://127.0.0.1:7864
```

Con il virtualenv attivo è lo stesso comando; oppure, senza attivarlo:

```bash
.venv/bin/python web_video2midi.py
```

Altre opzioni:

```bash
python3 web_video2midi.py --apri          # apre anche il browser da solo
python3 web_video2midi.py --porta 9000    # porta diversa
PORTA=9000 python3 web_video2midi.py      # idem, via variabile d'ambiente
```

Su macOS puoi anche fare doppio clic su `avvia_midi.command` (la prima volta:
`chmod +x avvia_midi.command`), su Windows doppio clic su `avvia_midi.bat`.

> **Importante — l'indirizzo `127.0.0.1` è "questo computer".**
> `http://127.0.0.1:7864` funziona solo sulla macchina dove il programma sta girando:
> non puoi aprire il mio sito dal tuo computer, devi avviarlo sul tuo. Il server
> ascolta apposta solo su `127.0.0.1`, quindi non è raggiungibile dalla rete locale.
> Per usarlo da un altro dispositivo della stessa rete bisogna avviarlo con
> `host="0.0.0.0"` (modifica la riga `app.run(...)` in fondo a `web_video2midi.py`) e
> aprire `http://IP-del-computer:7864`.

Il Terminale deve restare aperto mentre usi il sito. Per fermarlo: `Ctrl+C`.

---

## 5. Usare il sito

1. Apri <http://127.0.0.1:7864>.
2. Trascina il video del piano roll (o clicca per sceglierlo). Limite: 8 GB.
3. Imposta:
   * **BPM del file scritto** — metti lo stesso BPM del progetto FL Studio, così il
     DAW mostra le stesse battute. Se non lo sai, lascia 120: cambia solo la griglia
     dei tick, la musica suona identica.
   * **Ottava FL della riga più bassa** — si usa solo se le etichette dei tasti non
     vengono lette (di default 1, che è il valore giusto nella maggior parte dei
     casi con la tastiera di FL visibile).
4. Premi converti e guarda il log: durata/fps, pixel per semitono, velocità in px/s,
   numero di note riconosciute.
5. Alla fine scarichi il `.mid` e lo puoi **ascoltare subito** nella pagina: la
   pagina legge il MIDI da sola e lo suona con la Web Audio API, con il piano roll
   in stile FL sotto. Nessuna libreria esterna, nessun file audio.

Ogni conversione lascia i suoi file in `web_lavori/<id>/` (`video.mp4` + `song.mid`):
puoi cancellarli quando vuoi, sono solo copie di lavoro.

---

## 6. Riga di comando (senza sito)

```bash
# conversione semplice: crea video.mid accanto al video
python3 video2midi.py video.mp4

# nome del file, ottava di ripiego, BPM e immagini di controllo
python3 video2midi.py video.mp4 -o song.mid --ottava 4 --bpm 120 --debug debug_fl/
```

Opzioni: `-o` nome del MIDI · `--ottava` ottava FL della riga più in basso (serve
solo se l'OCR non legge le etichette — è il ripiego) · `--bpm` griglia dei tick ·
`--debug` salva `debug_griglia.png` e le immagini della tastiera · `-q` log più
silenzioso. `python3 video2midi.py --help` per il riepilogo.

Esempio di uscita reale (video di test 480×854, 30 fps, 428 frame, 14 s di musica):

```
   tastiera fino a x=42, piano roll da y=74
   semitono = 13.87 px, riga di riferimento y=844.4 (classe A# +/-12k)
   ottava: ricavata da 10 etichette lette sulla tastiera
   righe scandite: 56 (dal MIDI 22 al MIDI 77)
   velocita': 119.9 px di schermo per secondo di musica (scorrimento della griglia)
   osservazioni raccolte: 18516
   note riconosciute: 171 (durata 13.94 s)
MIDI scritto: song.mid  (171 note)
```

La conversione di quel video è durata **2,8 s**: non è pesante, non serve GPU.

---

## 7. Come registrare il piano roll (è la parte che decide la qualità)

* Registra **solo la finestra del piano roll** di FL Studio, con la tastiera a
  sinistra ben visibile e non coperta dalla toolbar.
* Il **playhead** (la linea verde) deve restare visibile mentre il brano suona:
  da lì il programma ricava lo scorrimento del tempo.
* Non mettere in pausa e non fermarti su un fotogramma: registra mentre la musica
  suona. Evita lo zoom che cambia durante la registrazione.
* 30 fps vanno benissimo. Un brano intero va bene; il silenzio iniziale non viene
  conservato.
* Se il video è una ripresa a mano dello schermo (non una cattura), il programma può
  sbagliare: è pensato per le catture, non per il telefono puntato sul monitor.

---

## 8. Errori tipici e rimedi

| Messaggio | Causa | Rimedio |
| --- | --- | --- |
| «griglia delle note non trovata: il video non sembra un piano roll di FL Studio» | tastiera non visibile, troppo scura, o il video non è un piano roll | registra la finestra del piano roll con la tastiera a sinistra |
| «non capisco come scorre il tempo in questo video (testina e griglia ferme)» | video fermo, playhead invisibile, riproduzione non partita | registra mentre suona, lascia visibile la testina verde |
| note tutte spostate di ottave | etichette dei tasti non lette | installa tesseract, oppure passa `--ottava N` |
| `serve mido per scrivere il MIDI` | manca la libreria | `python3 -m pip install -r requirements.txt` |
| `ModuleNotFoundError: cv2` / `flask` | manca la libreria | idem sopra |
| la pagina si apre ma la conversione dà errore | il `.py` lanciato non trova `video2midi.py` | i due file devono stare nella stessa cartella |

---

## 9. Limiti, detti chiaramente

* Funziona sui **piano roll di FL Studio** (tastiera a sinistra, note colorate,
  testina verde). Altri DAW non sono supportati così come sono.
* Non legge l'audio: se una nota non è disegnata nella griglia, per il programma non
  esiste. Note coperte, fuori schermo o coperte dalla toolbar vanno perse.
* Il MIDI esce come tipo 0, 480 tick per battuta, con un evento di tempo iniziale
  (nessuna dinamica/velocity: le note sono tutte uguali).
* Le note "C5" di FL corrispondono al DO centrale: qui il MIDI è scritto nella
  convenzione standard (DO centrale = C4 / MIDI 60), quindi una nota che FL chiama
  "F4" esce come F3. È normale, non è un errore di ottava.
* La pagina contiene anche il README tecnico completo (come viene trovata la
  griglia, i semitoni, il modello `T = t + (x − xp)/ppt`, le note dai fotogrammi).

