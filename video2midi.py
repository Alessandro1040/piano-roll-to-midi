#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video2midi.py - da un video di FL Studio (piano roll) a un file MIDI.

E' la versione "tutta in un file" del programma di riferimento
(register.py / extract.py / extract2.py / analysis.py): stessa idea di base
(tastiera -> griglia dei semitoni, testina -> tempo, testine delle note ->
note), ma senza la registrazione SIFT (inutile se il video e' una
registrazione dello schermo, non una ripresa a mano) e senza la tabella di
riferimento precalcolata, cosi' funziona su un video qualsiasi.

Come funziona, in breve:
  1. dalla tastiera disegnata a sinistra ricava la griglia dei semitoni (i
     tasti neri sono righe scure) e la classe di altezza di ogni riga;
  2. legge l'ottava con tesseract (le etichette tipo "F4" sulla tastiera) e,
     se non ci riesce, usa l'ottava indicata con --ottava;
  3. insegue la testina (la linea verde del playhead) per sapere a che punto
     del tempo si trova ogni x dello schermo;
  4. su ogni riga della griglia cerca le testine delle note (pixel chiari
     della griglia colorata) e ne misura inizio e fine;
  5. converte x -> tempo con il modello   T = t + (x - xp(t)) / ppt
     che vale sia quando la testina scorre sia quando la griglia scorre
     sotto la testina ferma (l'auto-scroll di FL Studio);
  6. raggruppa le osservazioni fatte su tutti i frame e scrive il MIDI.

Uso:
    python3 video2midi.py video.mp4                    # -> video.mid
    python3 video2midi.py video.mp4 -o out.mid
    python3 video2midi.py video.mp4 --ottava 4         # ottava FL di base
    python3 video2midi.py video.mp4 --debug debug_fl/  # salva delle immagini

Nota sulle ottave: FL Studio chiama "C5" il DO centrale (MIDI 60); qui il MIDI
viene scritto nella convenzione standard (DO centrale = C4, MIDI 60), quindi
una nota "F4" di FL Studio esce come F3.
"""

import argparse
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

try:
    import mido
except ImportError:                                  # pragma: no cover
    mido = None

NOMI = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
NOMI_ALTERATI = {1, 3, 6, 8, 10}                     # classi dei tasti neri


# ----------------------------------------------------------------------------
#  utilita' di base
# ----------------------------------------------------------------------------
def log(*a):
    print(*a, flush=True)


def runs_con_salti(col, salta, minimo):
    """Segmenti (a, b) di una maschera 1-D, unendo i buchi <= `salta`."""
    out = []
    st = None
    ultimo = None
    buco = 0
    for x in range(len(col)):
        if col[x]:
            if st is None:
                st = x
            buco = 0
            ultimo = x
        elif st is not None:
            buco += 1
            if buco > salta:
                if ultimo - st + 1 >= minimo:
                    out.append((st, ultimo))
                st = None
                ultimo = None
    if st is not None and ultimo - st + 1 >= minimo:
        out.append((st, ultimo))
    return out



# ----------------------------------------------------------------------------
#  1) zona della tastiera e inizio del piano roll
# ----------------------------------------------------------------------------
def _y_top_da_testina(video, kb_end, H, W):
    """La testina e' una linea verticale che parte dal bordo alto del roll:
    la sua cima e' esattamente l'inizio del piano roll."""
    if not video or not os.path.exists(str(video)):
        return None
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return None
    nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    alti = []
    try:
        for frac in (0.10, 0.25, 0.40, 0.55, 0.70, 0.85):
            i = int((nfr or 100) * frac)
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, fr = cap.read()
            if not ok:
                continue
            hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
            m = maschera_testina(hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2], kb_end, 0)
            col = m.sum(0)
            x = int(col.argmax())
            if col[x] < 0.30 * H:
                continue
            righe = np.where(m[:, max(0, x - 1):x + 2].max(1) > 0)[0]
            if len(righe) == 0:
                continue
            alti.append(int(righe.min()))
    finally:
        cap.release()
    alti = [a for a in alti if 0.02 * H <= a <= 0.4 * H]
    if len(alti) < 2:
        return None
    alti.sort()
    return int(np.median(alti))


def _y_top_da_profilo(im, kb_end):
    """Ripiego: la prima fascia scura lunga sotto la barra strumenti."""
    H = im.shape[0]
    v = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[:, :, 2]
    x0 = max(kb_end + 2, int(im.shape[1] * 0.08))
    rowv = v[:, x0:].mean(1)
    fila = 0
    for y in range(int(H * 0.03), H):
        if rowv[y] < 90:
            fila += 1
            if fila >= 15:
                return int(y - 14)
        else:
            fila = 0
    return int(H * 0.09)


def trova_zone(video, im):
    """Ritorna (kb_end, y_top): ultima colonna della tastiera, prima riga del roll."""
    H, W = im.shape[:2]
    v = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[:, :, 2]
    colv = np.median(v[int(H * 0.15):, :], axis=0)
    kb_end = 0
    for x in range(int(W * 0.35)):              # la tastiera e' la fascia chiara a sinistra
        if colv[x] > 120:
            kb_end = x
    y_top = _y_top_da_testina(video, int(kb_end), H, W)
    if y_top is None:
        y_top = _y_top_da_profilo(im, int(kb_end))
    return int(kb_end), int(y_top)



# ----------------------------------------------------------------------------
#  2) griglia dei semitoni (righe dei tasti neri) + classe di altezza
# ----------------------------------------------------------------------------
def righe_scure(profilo, y_top, y_bot, minimo=5):
    """Centri (y) delle righe piu' scure di un profilo verticale."""
    prof = profilo[int(y_top):int(y_bot)].astype(np.float32)
    if prof.max() - prof.min() < 4:
        return []
    soglia = (prof.min() + prof.max()) / 2.0
    out = []
    st = None
    for i, d in enumerate(prof < soglia):
        if d and st is None:
            st = i
        if (not d) and st is not None:
            if i - st >= minimo:
                out.append(float(y_top + (st + i - 1) / 2.0))
            st = None
    return out


def griglia(im, kb_end, y_top):
    """Trova la griglia dei semitoni dai tasti neri della tastiera.

    Ritorna (y_rif, h, pc_rif): y del tasto nero piu' basso, altezza in pixel
    di un semitono e classe di altezza (0..11) di quella riga.  None se fallisce.
    """
    H, W = im.shape[:2]
    v = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[:, :, 2]
    cand = []
    if kb_end >= 8:                                  # 1a scelta: la tastiera
        strip = np.median(v[:, 2:max(3, kb_end - 4)], axis=1)
        cand = righe_scure(strip, y_top - 10, H)
    if len(cand) < 6:                                # ripiego: le strisce del roll
        m = max(kb_end + 3, W // 2 - 40)
        med = np.median(v[:, m:max(kb_end + 4, W // 2 + 40)], axis=1)
        cand = righe_scure(med, y_top + 2, H)
    cand = [y for y in cand if y > y_top - 12]
    if len(cand) < 3:
        return None
    cand = np.array(sorted(cand))
    salti = [cand[i + 1] - cand[i] for i in range(len(cand) - 1)]
    salti = [s for s in salti if s > 3]
    if not salti:
        return None
    h0 = min(salti) / 2.0                            # i tasti neri distano almeno 2 semitoni
    if h0 < 3:
        h0 = float(np.median(salti)) / 2.5
    off = [0.0]
    for s in salti:
        off.append(off[-1] + round(s / h0))          # semitoni *sotto* il primo tasto nero
    off = np.array(off)
    ob = off[-1] - off                               # semitoni *sopra* l'ultimo tasto nero
    A = np.stack([np.ones_like(ob), -ob], 1)         # y = y_rif - h * ob  (h > 0)
    sol, *_ = np.linalg.lstsq(A, cand, rcond=None)
    y_rif, h = float(sol[0]), float(sol[1])
    if not (3.0 < h < 60.0):
        return None
    classi = [int(round(o)) % 12 for o in ob]
    pc_rif = None
    for c in range(12):                              # la tavola dei tasti neri
        if set((c + k) % 12 for k in classi) == NOMI_ALTERATI:
            pc_rif = c
            break
    if pc_rif is None:
        return None
    return y_rif, h, pc_rif



# ----------------------------------------------------------------------------
#  3) ottava di riferimento (etichette lette con tesseract)
# ----------------------------------------------------------------------------
LETTERE = {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F', 'G': 'G',
           '£': 'F', '6': 'G', '8': 'B', '0': 'D', 'O': 'D', 'Q': 'D'}
DIGITI = {'0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', '6': '6',
          '7': '7', '8': '8', 'O': '0', 'o': '0', 'Q': '0', 'D': '0', 'U': '0',
          'S': '5', 's': '5', 'B': '8', 'b': '8', 'I': '1', 'l': '1', '|': '1',
          '!': '1', 'i': '1', 'T': '7', 'Z': '2', 'z': '2', 'g': '9', 'G': '6',
          '?': '7', 'J': '1', 'L': '1', 'A': '4', 'E': '5', 'e': '5', 'C': '6'}


def normalizza_etichetta(txt):
    """Da una parola OCR tipo "£6"/"BS" prova a tirar fuori "F6"/"B5"."""
    t = ''.join(ch for ch in txt if ch.isalnum() or ch == '£')
    if len(t) < 2:
        return None
    c = LETTERE.get(t[0].upper())
    d = DIGITI.get(t[1])
    if c is None or d is None:
        return None
    return c + d


def ocr_etichette(im, kb_end, y_top, cartella):
    """Legge le etichette dei tasti (FL: "C5" = MIDI 60). Lista di (y, midi)."""
    exe = None
    for p in ('tesseract', '/opt/homebrew/bin/tesseract', '/usr/local/bin/tesseract'):
        if os.path.sep in p:
            if os.path.exists(p):
                exe = p
                break
        else:
            from shutil import which
            exe = which(p)
            if exe:
                break
    if exe is None or kb_end < 12:
        return []
    x0 = max(0, kb_end - 17)                     # le etichette stanno a destra dei tasti
    x1 = min(im.shape[1], kb_end + 1)
    y0 = max(0, y_top - 4)
    crop = im[y0:im.shape[0], x0:x1]
    if crop.size == 0 or crop.shape[0] < 20:
        return []
    sc = 8
    grande = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_LANCZOS4)
    grigio = cv2.cvtColor(grande, cv2.COLOR_BGR2GRAY)
    bw = cv2.threshold(grigio, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    png = os.path.join(cartella, 'etichette_tastiera.png')
    if not cv2.imwrite(png, bw):
        return []
    out = []
    for psm in ('11', '6'):                      # 11 = testo sparso, 6 = blocco unico
        try:
            r = subprocess.run([exe, png, 'stdout', '--psm', psm, 'tsv'],
                               capture_output=True, timeout=120)
        except Exception:
            continue
        for riga in r.stdout.decode('utf-8', 'replace').splitlines()[1:]:
            f = riga.split('\t')
            if len(f) < 12 or not f[11].strip():
                continue
            try:
                if float(f[10]) < 35:            # confidenza troppo bassa
                    continue
                yc = y0 + (float(f[7]) + float(f[9]) / 2.0) / sc
            except ValueError:
                continue
            lab = normalizza_etichetta(f[11].strip())
            if lab is None:
                continue
            out.append((float(yc), NOMI.index(lab[0]) + 12 * int(lab[1])))
        if len(out) >= 3:
            break
    out.sort()
    return out



def ancora_da_etichette(etichette, y_rif, h, pc_rif):
    """MIDI del tasto nero piu' basso (y_rif), dedotto dalle etichette lette."""
    voti = []
    for y, midi in etichette:
        k = int(round((y_rif - y) / h))
        if (midi - k) % 12 != pc_rif:                # non torna con la griglia: scarto
            continue
        voti.append(midi - k)
    if not voti:
        return None
    return int(round(float(np.median(voti))))


def ancora_manuale(H, y_rif, h, pc_rif, ottava):
    """--ottava = ottava FL della riga piu' in basso, trattata come un C.

    In FL Studio il do centrale si chiama C5 (MIDI 60), quindi la riga piu' in
    basso vale 12 * ottava in MIDI."""
    k = int(round((y_rif - (H - 2)) / h))
    return 12 * int(ottava) - k



# ----------------------------------------------------------------------------
#  4) la testina (playhead) e i pixel al secondo di tempo musicale
# ----------------------------------------------------------------------------
def maschera_testina(h, s, v, kb_end, y_top):
    m = ((h > 36) & (h < 62) & (s > 110) & (v > 34) & (v < 135)).astype(np.uint8)
    m[:y_top, :] = 0
    m[:, :kb_end + 3] = 0
    return m


def insegui_testine(video, fps, nfr, kb_end, y_top, H, passo=1):
    """Posizione x della testina per ogni frame campionato: [(t, x|None)]."""
    cap = cv2.VideoCapture(video)
    out = []
    i = 0
    hmin = 0.25 * (H - y_top)
    while nfr == 0 or i < nfr:
        ok, fr = cap.read()
        if not ok:
            break
        if i % passo == 0:
            hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
            m = maschera_testina(hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2], kb_end, y_top)
            col = m.sum(0)
            x = int(col.argmax())
            out.append((i / fps, x if col[x] >= hmin else None))
        i += 1
    cap.release()
    return out


def stima_ppt(testine, salto=10):
    """Pixel di schermo per secondo di musica, dalla rampa della testina.

    Il timing dei frame (registrazione dello schermo ricodificata) non e'
    regolare e fra due frame vicini la testina si sposta di pochi pixel:
    il confronto va fatto fra frame distanti un terzo di secondo circa."""
    camp = [(t, x) for t, x in testine if x is not None]
    vel = []
    for i in range(len(camp)):
        for j in range(i + salto, min(i + salto + 3, len(camp))):
            dt = camp[j][0] - camp[i][0]
            dx = camp[j][1] - camp[i][1]
            if dt > 0 and dx > 8:                    # dx<0: il loop e' ripartito
                vel.append(dx / dt)
    if not vel:
        return None
    return float(np.median(vel))


def ppt_da_scorrimento(video, fps, nfr, kb_end, y_top, H, W, xsr=None, salto=12):
    """Pixel di schermo per secondo di musica, misurati dallo scorrimento della
    griglia: la correlazione di fase fra due frame dice di quanto si e' spostato
    il disegno del piano roll.

    Il confronto e' fatto su frame distanti ~mezzo secondo, perche' il timing
    dei video (registrazioni dello schermo ricodificate) non e' regolare e la
    misura su un solo frame e' troppo rumorosa.  La colonna della testina viene
    cancellata: e' ferma e tirerebbe la misura verso zero."""
    if nfr < 3 * salto:
        return None
    cap = cv2.VideoCapture(video)
    vals = []
    ya, xa = y_top + 4, kb_end + 4
    try:
        for frac in (0.15, 0.27, 0.39, 0.51, 0.63, 0.75, 0.87):
            i0 = int(nfr * frac)
            i1 = min(nfr - 1, i0 + salto)
            if i1 - i0 < 4:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, i0)
            ok, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_FRAMES, i1)
            ok2, b = cap.read()
            if not ok or not ok2:
                continue
            ga = cv2.cvtColor(a[ya:H - 4, xa:W - 4], cv2.COLOR_BGR2GRAY).astype(np.float32)
            gb = cv2.cvtColor(b[ya:H - 4, xa:W - 4], cv2.COLOR_BGR2GRAY).astype(np.float32)
            if ga.shape != gb.shape or min(ga.shape) < 8:
                continue
            for g, i in ((ga, i0), (gb, i1)):
                if xsr is not None and i < len(xsr) and not np.isnan(xsr[i]):
                    c = int(round(xsr[i])) - xa
                    if 0 <= c < g.shape[1]:
                        g[:, max(0, c - 8):c + 9] = float(np.median(g))
            (dx, _), r = cv2.phaseCorrelate(ga, gb)
            if r < 0.30 or abs(dx) < 2.0:            # fermo, o misura inaffidabile
                continue
            vals.append(abs(dx) / ((i1 - i0) / float(fps)))
    finally:
        cap.release()
    if not vals:
        return None
    return float(np.median(vals))


# ----------------------------------------------------------------------------
#  5) note presenti in un frame
# ----------------------------------------------------------------------------
def osserva_frame(v, t, xp, righe, x0, W, H, ppt, mezza_testina=9):
    """Osservazioni (midi, t_inizio, t_fine|None) dalle testine di un frame."""
    oss = []
    if xp is None or ppt is None:
        return oss
    xp = float(xp)
    for yc, midi in righe:
        ya, yb = max(0, yc - 3), min(H, yc + 4)
        if yb - ya < 5:
            continue
        band = v[ya:yb, x0:W].astype(np.float32)
        lo = int(xp) - mezza_testina - x0                      # dove passa la testina
        hi = int(xp) + mezza_testina + 1 - x0
        lo, hi = max(0, lo), min(band.shape[1], hi)
        sel = np.ones(band.shape[1], bool)
        if hi > lo:
            sel[lo:hi] = False
        bg = np.median(band[:, sel] if sel.any() else band, axis=1, keepdims=True)
        diff = band - bg
        test = ((diff > 55) & (band > 110)).mean(0) > 0.35     # testine: pixel chiari
        corpo = ((diff > 9) & (band > 45)).mean(0) > 0.55      # corpo: un po' piu' chiaro
        nota = test | corpo
        if hi > lo:
            test[lo:hi] = False
            corpo[lo:hi] = False
            nota[lo:hi] = False
        runs = runs_con_salti(nota, 2, 4)                      # estensione delle note
        teste = runs_con_salti(test, 2, 3)                     # testine (inizio nota)

        def tempo(x):                                          # x locale -> tempo musicale
            return t + (x + x0 - xp) / ppt

        def emetti(xa, xb):
            if xa + x0 <= x0 + 2:                              # tagliata dal bordo sinistro
                return
            t_fin = None
            if xb + x0 < W - 3 and not (xa + x0 < xp < xb + x0):
                t_fin = tempo(xb)
            oss.append((int(midi), float(tempo(xa)),
                        None if t_fin is None else float(t_fin)))

        for ra, rb in runs:
            dentro = [h for h in teste if ra - 1 <= h[0] <= rb + 1]
            if not dentro:
                continue                                       # corpo senza testina: griglia
            for k, (ha, _hb) in enumerate(dentro):             # una nota per ogni testina
                fine = rb if k + 1 >= len(dentro) else min(rb, dentro[k + 1][0] - 2)
                emetti(ha, fine)
    return oss


# ----------------------------------------------------------------------------
#  6) dalle osservazioni alle note, e note -> MIDI
# ----------------------------------------------------------------------------
def raggruppa(oss, toll=0.09):
    """Raggruppa le osservazioni (midi, t_in, t_fin) in note."""
    note = []
    for p in sorted(set(o[0] for o in oss)):
        L = sorted([o for o in oss if o[0] == p], key=lambda o: o[1])
        gruppi, cur = [], []
        for o in L:
            if cur and o[1] - cur[-1][1] > toll:
                gruppi.append(cur)
                cur = []
            cur.append(o)
        if cur:
            gruppi.append(cur)
        for gr in gruppi:
            if len(gr) < 2:                                  # una sola occhiata: rumore
                continue
            inizio = float(np.median([o[1] for o in gr]))
            fini = [o[2] for o in gr if o[2] is not None and o[2] > inizio + 0.03]
            fine = float(np.median(fini)) if len(fini) >= 2 else inizio + 0.12
            if fine - inizio > 2.0:
                fine = inizio + 2.0
            note.append((inizio, fine, int(p)))
    note.sort()
    return note


def scrivi_midi(note, out, bpm=120.0, ticks=480):
    """Scrive le note (inizio, fine, midi) in un file MIDI tipo 0."""
    if mido is None:
        raise SystemExit('serve mido per scrivere il MIDI:  pip install mido')
    tps = ticks * bpm / 60.0                                  # tick per secondo
    mid = mido.MidiFile(ticks_per_beat=ticks)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm), time=0))
    ev = []
    for st, en, p in note:
        ev.append((st, 1, p))
        ev.append((en, 0, p))
    ev.sort(key=lambda e: (e[0], e[1]))
    tprev = 0
    for t, on, p in ev:
        tick = int(round(t * tps))
        tr.append(mido.Message('note_on' if on else 'note_off', note=int(p),
                               velocity=100 if on else 0, time=max(0, tick - tprev)))
        tprev = tick
    mid.save(out)
    return len(note)


def interpola_testine(testine, fps, nfr, passo):
    """Posizione della testina per ogni frame (NaN dove non si vede)."""
    n = nfr if nfr else int(testine[-1][0] * fps) + passo + 1
    x = np.full(n, np.nan)
    for t, xx in testine:
        if xx is None:
            continue
        i = int(round(t * fps))
        if 0 <= i < n:
            x[i] = xx
    buoni = np.where(~np.isnan(x))[0]
    if len(buoni) == 0:
        return x
    if len(buoni) == 1:
        x[:] = x[buoni[0]]
        return x
    return np.interp(np.arange(n), buoni, x[buoni])


def salva_debug(im0, righe, cartella, kb_end, y_top):
    out = im0.copy()
    for yc, midi in righe:
        col = (0, 0, 255) if midi % 12 == 0 else (0, 170, 255)
        cv2.line(out, (max(0, kb_end - 2), yc), (out.shape[1] - 1, yc), col, 1)
        if midi % 12 == 0:
            txt = '%s%d' % (NOMI[0], midi // 12)
            cv2.putText(out, txt, (out.shape[1] - 34, yc - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 255, 255), 1)
    cv2.imwrite(os.path.join(cartella, 'debug_griglia.png'), out)



# ----------------------------------------------------------------------------
#  7) tutto insieme
# ----------------------------------------------------------------------------
def analizza(video, ottava=1, debug=None, passo=None, verbose=True):
    """Da un video a una lista di note (inizio, fine, midi)."""
    def p(*a):
        if verbose:
            log(*a)

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit('non riesco ad aprire %s' % video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, im0 = cap.read()
    cap.release()
    if not ok:
        raise SystemExit('video senza frame leggibili: %s' % video)
    p('   video %dx%d, %.1f fps, %d frame' % (W, H, fps, nfr))

    kb_end, y_top = trova_zone(video, im0)
    p('   tastiera fino a x=%d, piano roll da y=%d' % (kb_end, y_top))
    g = griglia(im0, kb_end, y_top)
    if g is None:
        raise SystemExit("griglia delle note non trovata: il video non sembra "
                         "un piano roll di FL Studio")
    y_rif, hpx, pc_rif = g
    p('   semitono = %.2f px, riga di riferimento y=%.1f (classe %s +/-12k)'
      % (hpx, y_rif, NOMI[pc_rif]))

    cartella = debug or tempfile.mkdtemp(prefix='video2midi_')
    os.makedirs(cartella, exist_ok=True)
    try:
        etichette = ocr_etichette(im0, kb_end, y_top, cartella)
        ref = ancora_da_etichette(etichette, y_rif, hpx, pc_rif)
        if ref is not None:
            p('   ottava: ricavata da %d etichette lette sulla tastiera' % len(etichette))
        else:
            ref = ancora_manuale(H, y_rif, hpx, pc_rif, ottava)
            p('   ottava: etichette non riconosciute, uso --ottava %d' % ottava)

        righe = []
        jmin = int(np.floor((y_rif - (H - 2)) / hpx)) - 1
        jmax = int(np.ceil((y_rif - y_top) / hpx)) + 1
        for j in range(jmin, jmax + 1):
            yc = int(round(y_rif - hpx * j))
            midi = ref + j
            if 0 <= midi <= 127 and y_top - 2 <= yc <= H - 3:
                righe.append((yc, int(midi)))
        p('   righe scandite: %d (dal MIDI %d al MIDI %d)'
          % (len(righe), min(r[1] for r in righe), max(r[1] for r in righe)))
        if debug:
            salva_debug(im0, righe, cartella, kb_end, y_top)

        if passo is None:
            passo = max(1, (nfr or 900) // 900)
        testine = insegui_testine(video, fps, nfr, kb_end, y_top, H, passo=passo)
        xsr = interpola_testine(testine, fps, nfr, passo)
        ppt = ppt_da_scorrimento(video, fps, nfr, kb_end, y_top, H, W, xsr)
        if ppt:
            p("   velocita': %.1f px di schermo per secondo di musica "
              "(scorrimento della griglia)" % ppt)
        else:
            ppt = stima_ppt(testine)
            if ppt:
                p("   velocita': %.1f px di schermo per secondo di musica "
                  "(rampa della testina)" % ppt)
        if not ppt or ppt < 1:
            raise SystemExit('non capisco come scorre il tempo in questo video '
                             '(testina e griglia ferme)')

        cap = cv2.VideoCapture(video)
        oss = []
        i = 0
        while nfr == 0 or i < nfr:
            ok, fr = cap.read()
            if not ok:
                break
            if i % passo == 0:
                v = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)[:, :, 2]
                xp = xsr[i] if i < len(xsr) else None
                if xp is not None and not np.isnan(xp):
                    oss.extend(osserva_frame(v, i / fps, float(xp), righe,
                                             kb_end + 2, W, H, ppt))
            i += 1
        cap.release()
    finally:
        if debug is None:
            try:
                import shutil
                shutil.rmtree(cartella)
            except Exception:
                pass
    p('   osservazioni raccolte: %d' % len(oss))
    note = raggruppa(oss)
    if not note:
        raise SystemExit('nessuna nota riconosciuta in questo video')
    t0 = min(n[0] for n in note)                     # il MIDI parte da 0
    note = [(max(0.0, st - t0), max(0.02, en - t0), p) for st, en, p in note]
    p('   note riconosciute: %d (durata %.2f s)' % (len(note), note[-1][1]))
    return note


def main():
    ap = argparse.ArgumentParser(description='Da un video di FL Studio (piano roll) a un MIDI')
    ap.add_argument('video')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--ottava', type=int, default=1,
                    help="ottava FL della riga piu' in basso (trattata come un C); "
                         "serve solo se le etichette della tastiera non si leggono")
    ap.add_argument('--bpm', type=float, default=120.0,
                    help='bpm del file scritto (cambia solo la griglia temporale)')
    ap.add_argument('--debug', default=None, help='cartella per le immagini di controllo')
    ap.add_argument('-q', '--quiet', action='store_true')
    a = ap.parse_args()
    out = a.out or (os.path.splitext(a.video)[0] + '.mid')
    log('video2midi: %s' % a.video)
    note = analizza(a.video, ottava=a.ottava, debug=a.debug, verbose=not a.quiet)
    n = scrivi_midi(note, out, bpm=a.bpm)
    log('MIDI scritto: %s  (%d note)' % (out, n))


if __name__ == '__main__':
    main()
