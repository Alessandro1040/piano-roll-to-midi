#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera una demo sintetica: un piano roll "in stile FL Studio" disegnato da
zero, piu' il MIDI di verita' (le note che ci abbiamo disegnato dentro).

Serve per avere una coppia video -> MIDI pubblicabile senza usare registrazioni
personali:

    python3 tools/genera_demo.py                      # frame in /tmp/demo_frames
    python3 tools/genera_demo.py --out-frames cartella

Poi il video si monta coi frame (ffmpeg serve solo qui, non per usare l'app):

    ffmpeg -framerate 30 -i cartella/%04d.png -c:v libx264 -pix_fmt yuv420p \
           -crf 20 -movflags +faststart docs/assets/demo_video.mp4

Il disegno rispetta le stesse regole che `video2midi.py` cerca in una vera
registrazione di FL Studio: tastiera chiara a sinistra (tasti neri = righe
scure), griglia dei semitoni, testina verde che scorre, note con la testina
piu' chiara del corpo.  Se cambi i colori, ricontrolla le maschere in
`video2midi.maschera_testina` e in `osserva_frame`.
"""

import argparse
import json
import os

import cv2
import numpy as np

# --- geometria del finto piano roll -----------------------------------------
W, H = 720, 1280          # verticale, come una cattura dello schermo del telefono
FPS = 30
DURATA = 6.5              # secondi
KB_W = 54                 # larghezza della tastiera (a sinistra)
Y_TOP = 140               # dove comincia il roll (sotto la "toolbar")
H_SEMI = 13.0             # altezza di un semitono in pixel
Y_BOTTOM = 1272           # centro dell'ultima riga in basso
X0 = 60                   # x della testina a t=0 (subito a destra della tastiera)
PPT = 110.0               # pixel di schermo per secondo di musica (scorre la testina)

# --- colori (BGR) -----------------------------------------------------------
BG_NAT = (24, 24, 24)     # riga dei tasti bianchi  (V=24)
BG_ALT = (30, 30, 30)     # riga dei tasti neri    (V=30)
GRIGLIA = (44, 44, 44)    # linee verticali dei beat (V=44: sotto la soglia "corpo")
TASTO_B = (208, 208, 208)  # tasto bianco (V=208, la tastiera e' la fascia chiara)
TASTO_N = (40, 40, 40)     # tasto nero  (riga scura della griglia)
TESTINA = (20, 120, 20)    # verde scuro: H=60, S=213, V=120 (dentro la maschera)
NOTA_CORPO = (40, 80, 100)  # V=100: piu' chiaro del fondo, ma non "testina"
NOTA_TESTA = (60, 140, 210)  # V=210: la testa della nota, chiara

NERI = {1, 3, 6, 8, 10}    # classi di altezza dei tasti neri


def e_nero(j):
    """La riga j semitoni sopra la piu' bassa e' un tasto nero?"""
    return j % 12 in NERI


def y_di(j):
    return Y_BOTTOM - H_SEMI * j


# La melodia della demo: (semitoni sopra la riga piu' bassa, inizio s, durata s).
# Sta volutamente dentro i bordi: niente note tagliate dal bordo destro.
MELODIA = [
    (12, 0.30, 0.40), (16, 0.30, 0.40),
    (14, 0.80, 0.40), (19, 0.80, 0.40),
    (16, 1.30, 0.35), (21, 1.30, 0.35),
    (17, 1.75, 0.35), (24, 1.75, 0.35),
    (19, 2.20, 0.45), (23, 2.20, 0.45),
    (17, 2.80, 0.35), (21, 2.80, 0.35),
    (16, 3.25, 0.35), (19, 3.25, 0.35),
    (14, 3.70, 0.60), (17, 3.70, 0.60),
    (12, 4.40, 0.90), (16, 4.40, 0.90),
]


def disegna_frame(i, note_area=True):
    """Un fotogramma: tastiera, righe, griglia, note e testina."""
    fr = np.zeros((H, W, 3), np.uint8)
    fr[:] = BG_NAT

    jmax = int((Y_BOTTOM - Y_TOP) / H_SEMI) + 1
    # righe del roll (i tasti neri un po' piu' chiari, come in FL)
    for j in range(jmax + 1):
        yc = y_di(j)
        y0, y1 = int(round(yc - H_SEMI / 2)), int(round(yc + H_SEMI / 2))
        if 0 <= y0 < H:
            fr[max(0, y0):min(H, y1), KB_W:] = BG_ALT if e_nero(j) else BG_NAT
    # linee verticali dei beat (ogni 0.5 s di musica)
    for k in range(0, 40):
        x = int(round(X0 + PPT * k * 0.5))
        if KB_W < x < W:
            fr[:Y_TOP, x:x + 1] = GRIGLIA      # sopra il roll: la toolbar
            fr[Y_TOP:, x:x + 1] = GRIGLIA

    # tastiera: i tasti bianchi sono la fascia chiara, i neri le righe scure
    for j in range(jmax + 1):
        yc = y_di(j)
        y0, y1 = int(round(yc - H_SEMI / 2)), int(round(yc + H_SEMI / 2))
        col = TASTO_N if e_nero(j) else TASTO_B
        fr[max(0, y0):min(H, y1), 0:KB_W] = col
    fr[:, KB_W - 1:KB_W] = (90, 90, 90)        # bordo fra tastiera e roll

    # "toolbar" scura in alto, cosi' il roll comincia a Y_TOP
    fr[0:Y_TOP, KB_W:] = (18, 18, 18)

    t = i / float(FPS)
    if note_area:
        for j, t0, dur in MELODIA:
            xa = int(round(X0 + PPT * t0))
            xb = int(round(X0 + PPT * (t0 + dur)))
            yc = y_di(j)
            ya = int(round(yc - H_SEMI / 2)) + 1
            yb = int(round(yc + H_SEMI / 2)) - 1
            cv2.rectangle(fr, (xa, ya), (xb, yb), NOTA_CORPO, -1)
            cv2.rectangle(fr, (xa, ya), (min(xa + 6, xb), yb), NOTA_TESTA, -1)

    xp = int(round(X0 + PPT * t))
    if KB_W <= xp < W - 2:
        fr[Y_TOP:H, xp:xp + 4] = TESTINA
    return fr


def main():
    ap = argparse.ArgumentParser(description='Genera la demo sintetica')
    ap.add_argument('--out-frames', default='/tmp/demo_frames')
    ap.add_argument('--verita', default=None,
                    help='file JSON dove scrivere le note vere (default: accanto ai frame)')
    a = ap.parse_args()
    os.makedirs(a.out_frames, exist_ok=True)
    n = int(round(DURATA * FPS))
    for i in range(n):
        cv2.imwrite(os.path.join(a.out_frames, '%04d.png' % i), disegna_frame(i))
    verita = a.verita or os.path.join(os.path.dirname(a.out_frames.rstrip('/')) or '.',
                                      'demo_verita.json')
    with open(verita, 'w', encoding='utf-8') as f:
        json.dump({'fps': FPS, 'durata': DURATA, 'ppt': PPT,
                   'note': [{'riga': j, 'inizio': t0, 'durata': d}
                            for j, t0, d in MELODIA]}, f, indent=2)
    print('frame scritti: %d in %s' % (n, a.out_frames))
    print('verita: %s (%d note disegnate)' % (verita, len(MELODIA)))


if __name__ == '__main__':
    main()
