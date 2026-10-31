# -*- coding: utf-8 -*-
"""Costruisce colab/piano_roll_to_midi.ipynb (JSON valido garantito)."""
import json
import os

C = []


def md(src):
    C.append({"cell_type": "markdown", "metadata": {}, "source": src.strip("\n").splitlines(True)})


def code(src):
    C.append({"cell_type": "code", "execution_count": None, "metadata": {},
              "outputs": [], "source": src.strip("\n").splitlines(True)})


md(r"""
# 🎹 Video → MIDI

**Registri lo schermo mentre il piano roll di FL Studio suona → scarichi un file `.mid`.**

Nessun modello da scaricare, nessuna GPU: il programma legge i pixel (tastiera →
altezza delle note, testina del playhead → tempo) con OpenCV e numpy.

**Come si usa:** `Runtime → Esegui tutto`, poi carica il video quando compare il
pulsante *Scegli file*. In fondo trovi il link per scaricare il MIDI.

<sub>Codice: <https://github.com/Alessandro1040/piano-roll-to-midi> · licenza MIT</sub>
""")

code(r"""
#@title 1 · Installa tutto (circa un minuto) { display-mode: "form" }
import os, sys, subprocess

REPO = "https://github.com/Alessandro1040/piano-roll-to-midi.git"
DEST = "/content/piano-roll-to-midi"

if not os.path.isdir(DEST):
    subprocess.run(["git", "clone", "-q", REPO, DEST], check=True)
os.chdir(DEST)
sys.path.insert(0, DEST)

# Tesseract legge le etichette della tastiera (C4, F#3…) per capire l'ottava.
# È facoltativo: senza, l'ottava si passa a mano nella cella 3.
if subprocess.run(["which", "tesseract"], capture_output=True).returncode != 0:
    subprocess.run("apt-get -qq install -y tesseract-ocr > /dev/null 2>&1", shell=True)

try:
    import cv2, numpy, mido
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "opencv-python-headless", "numpy", "mido"], check=True)
    import cv2, numpy, mido

print("cartella:", os.getcwd())
print("opencv", cv2.__version__, "| numpy", numpy.__version__, "| mido", mido.__version__)
print("tesseract:", "sì" if subprocess.run(["which", "tesseract"],
      capture_output=True).returncode == 0 else "no (uso --ottava)")
""")

md(r"""
### 2 · Carica il video

Registra lo schermo mentre il piano roll suona (mp4/mov/mkv/avi/webm), poi scegli
qui il file. Il video **non viene caricato da nessuna parte**: resta in questa
sessione Colab, che si spegne da sola.
""")

code(r"""
#@title 2 · Scegli il video del piano roll { display-mode: "form" }
from google.colab import files
import os

print("Scegli il video (mp4, mov, mkv, avi, webm):")
caricati = files.upload()

VIDEO = list(caricati.keys())[-1]
print("\nvideo:", VIDEO, "->", round(os.path.getsize(VIDEO) / 1e6, 2), "MB")
""")
md(r"""
### 3 · Converti

* **BPM**: non cambia le note, cambia solo la griglia del file `.mid` (utile per
  quantizzare in FL Studio). Metti il BPM del tuo progetto, o lascia 120.
* **Ottava**: serve solo se le etichette della tastiera non si leggono. È l'ottava
  FL della riga più in basso (di solito 1–6). Se le note escono un'ottava troppo
  alte o troppo basse, cambia questo numero.
""")

code(r"""
#@title 3 · Converti in MIDI { display-mode: "form" }
BPM = 120 #@param {type:"number"}
OTTAVA = 3 #@param {type:"number"}

import os

OUT = os.path.splitext(VIDEO)[0] + ".mid"
print("converto", VIDEO, "->", OUT, "\n")
!python video2midi.py "{VIDEO}" -o "{OUT}" --bpm {BPM} --ottava {int(OTTAVA)}
""")

md(r"""
### 4 · Guarda e scarica

Il grafico è il piano roll ricostruito **dal MIDI prodotto** (non dal video): se
somiglia a quello che hai suonato, è andata bene.
""")

code(r"""
#@title 4 · Piano roll + download del .mid { display-mode: "form" }
import os
import mido
import matplotlib.pyplot as plt
from google.colab import files

mid = mido.MidiFile(OUT)
t, acceso, note = 0.0, {}, []
for msg in mid:
    t += msg.time
    if msg.type == "note_on" and msg.velocity > 0:
        acceso[msg.note] = t
    elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
        if msg.note in acceso:
            t0 = acceso.pop(msg.note)
            note.append((msg.note, t0, t - t0))

print("note nel MIDI:", len(note))
if not note:
    print("⚠️ nessuna nota: guarda i messaggi della cella 3 qui sopra.")
else:
    nome = {0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",
            6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B"}
    lo = min(n[0] for n in note) - 1
    hi = max(n[0] for n in note) + 1
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor="#0f1011")
    ax.set_facecolor("#1a1b1e")
    for p, a, d in note:
        ax.add_patch(plt.Rectangle((a, p - 0.4), max(d, 0.03), 0.8,
                                   color="#e0553c", linewidth=0))
    ax.set_xlim(0, max(a + d for _, a, d in note) * 1.02)
    ax.set_ylim(lo, hi)
    ax.set_yticks(range(lo, hi + 1))
    ax.set_yticklabels(["%s%d" % (nome[p % 12], p // 12 - 1) for p in range(lo, hi + 1)],
                       fontsize=7, color="#b8b2a9")
    ax.tick_params(colors="#8a857d", labelsize=8)
    for s in ax.spines.values():
        s.set_color("#2c2e32")
    ax.set_xlabel("secondi", color="#b8b2a9", fontsize=9)
    ax.set_title("%s · %d note" % (os.path.basename(OUT), len(note)),
                 color="#f2ece4", loc="left", fontsize=11)
    plt.tight_layout()
    plt.show()

    files.download(OUT)
    print("\nTrascina", os.path.basename(OUT), "in FL Studio (o Ableton/Logic/MuseScore).")
""")

md(r"""
### Se qualcosa non va

| messaggio | cosa significa | rimedio |
| --- | --- | --- |
| *griglia delle note non trovata: il video non sembra un piano roll di FL Studio* | la tastiera o le righe non si vedono | inquadra il piano roll più grande, senza zoom strani |
| *non capisco come scorre il tempo in questo video (testina e griglia ferme)* | nessun movimento: il tempo non è misurabile | registra mentre il playhead scorre, o attiva l'auto-scroll |
| *nessuna nota riconosciuta in questo video* | note troppo piccole o del colore del fondo | ingrandisci il piano roll, usa colori chiari per le note |
| note un'ottava sbagliate | etichette non lette | cambia **Ottava** nella cella 3 |
| note doppie o spezzate | testina lenta, note vicine | registra a schermo intero, senza zoom durante la riproduzione |

Altri dettagli: [README](https://github.com/Alessandro1040/piano-roll-to-midi#readme) ·
[guida in italiano](https://github.com/Alessandro1040/piano-roll-to-midi/blob/main/docs/guida.html)
""")

nb = {
    "cells": C,
    "metadata": {
        "colab": {"provenance": [], "toc_visible": True, "name": "piano_roll_to_midi.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 0,
}

out = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "colab", "piano_roll_to_midi.ipynb"))
with open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("scritto", out, "(%d celle)" % len(C))
