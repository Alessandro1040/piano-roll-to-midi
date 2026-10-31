#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Da un MIDI ricava gli asset della demo: note in JSON (per il player della
pagina) e un'anteprima audio WAV sintetizzata al volo.

    python3 tools/esporta_demo.py docs/assets/demo_song.mid

Scrive accanto al MIDI:
  * `<nome>.json`  -> {"durata": s, "note": [{"midi": 60, "inizio": s, "durata": s}]}
  * `<nome>.wav`   -> anteprima con un synth additivo minimo (niente soundfont)

Il WAV serve solo alla pagina demo: l'app vera non lo produce, il MIDI sì.
"""

import argparse
import json
import math
import os
import struct
import wave

import mido

RATE = 22050
ARMONICHE = (1.0, 0.5, 0.22, 0.11)      # un timbro "di pianoforte" molto grezzo


def leggi_note(path):
    """Note (midi, inizio, durata) in secondi, con il tempo del file."""
    mid = mido.MidiFile(path)
    t = 0.0
    acceso = {}
    note = []
    for msg in mid:
        t += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            acceso[msg.note] = t
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if msg.note in acceso:
                t0 = acceso.pop(msg.note)
                note.append((int(msg.note), t0, max(0.02, t - t0)))
    # note rimaste accese (file troncato): chiudile alla fine
    for p, t0 in acceso.items():
        note.append((int(p), t0, 0.25))
    # ricalcola la durata dall'inizio all'ultima fine, non da t assoluto
    note = [(p, max(0.0, a), d) for p, a, d in note]
    note.sort(key=lambda n: (n[1], n[0]))
    if note:
        t0 = min(n[1] for n in note)
        note = [(p, a - t0, d) for p, a, d in note]
    return note


def sintetizza(note, out, coda=0.25):
    """Anteprima audio: somma di armoniche con inviluppo ADSR minimo."""
    durata = max([a + d for _, a, d in note] or [0.5]) + coda
    n = int(durata * RATE) + 1
    buf = [0.0] * n
    for p, a, d in note:
        f = 440.0 * 2.0 ** ((p - 69) / 12.0)
        i0 = int(a * RATE)
        ln = min(int(d * RATE), n - i0)
        if ln <= 0:
            continue
        att = max(1, int(0.006 * RATE))
        rel = max(1, int(0.05 * RATE))
        for k, amp in enumerate(ARMONICHE, start=1):
            if f * k > RATE / 2.0:
                break
            w = 2.0 * math.pi * f * k / RATE
            for i in range(ln):
                env = 1.0
                if i < att:
                    env = i / att
                elif i > ln - rel:
                    env = max(0.0, (ln - i) / rel)
                env *= (1.0 - 0.55 * (i / ln))          # decadimento naturale
                buf[i0 + i] += amp * env * math.sin(w * i)
    picco = max(1e-9, max(abs(x) for x in buf))
    dato = b''.join(struct.pack('<h', int(32000 * x / picco)) for x in buf)
    with wave.open(out, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(dato)
    return durata


def main():
    ap = argparse.ArgumentParser(description='Asset della demo da un MIDI')
    ap.add_argument('midi')
    a = ap.parse_args()
    base = os.path.splitext(a.midi)[0]
    note = leggi_note(a.midi)
    out_json = base + '.json'
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({'durata': round(max([x[1] + x[2] for x in note] or [0.5]), 3),
                   'note': [{'midi': p, 'inizio': round(s, 3), 'durata': round(d, 3)}
                            for p, s, d in note]}, f, indent=1)
    out_wav = base + '.wav'
    durata = sintetizza(note, out_wav)
    print('note: %d' % len(note))
    print('json: %s' % out_json)
    print('wav : %s (%.2f s, %.0f KB)' % (out_wav, durata, os.path.getsize(out_wav) / 1024))


if __name__ == '__main__':
    main()
