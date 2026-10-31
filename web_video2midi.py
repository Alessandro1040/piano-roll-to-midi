#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""=============================================================================
 SITO "VIDEO -> MIDI"  -  video2midi.py (video di FL Studio / piano roll)

 Pagina unica: trascini un video .mp4 (o .mov, .mkv, ...), il server lancia
 `video2midi.py` come sottoprocesso e, quando ha finito, ti da' il .mid da
 scaricare insieme al log della conversione. In piu' il risultato si puo'
 ASCOLTARE subito: la pagina legge il .mid da sola (parser Standard MIDI File
 scritto in JS) e lo suona con la Web Audio API del browser — nessuna libreria,
 nessun CDN, nessun file audio scaricato. Sotto il player compare anche il
 piano roll in stile FL Studio (canvas): tastiera, griglia dei tempi, note e
 testina sincronizzata con l'ascolto.

 USO
   python3 web_video2midi.py                 # apre http://127.0.0.1:7864
   PORTA=9000 python3 web_video2midi.py      # porta diversa
   python3 web_video2midi.py --apri          # apre anche il browser

 PERCHE' UN SITO E NON SOLO IL CLI
 video2midi.py legge il piano roll di FL Studio: tastiera a sinistra, testina
 del playhead, note colorate. Da browser non si puo' lanciare Python, quindi
 il lavoro lo fa questo server (locale) e la pagina si limita a fare da
 interfaccia: upload, avanzamento, download.

 Le conversioni girano UNA ALLA VOLTA (un semaforo): ognuna satura un core e
 mandarne due insieme non le rende piu' veloci, le rende solo piu' confuse da
 seguire nel log.
============================================================================="""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file

BASE = Path(__file__).resolve().parent
LAVORI = BASE / "web_lavori"
LAVORI.mkdir(exist_ok=True)
PROGRAMMA = BASE / "video2midi.py"

PORTA = int(os.environ.get("PORTA", "7864"))
ESTENSIONI = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg",
              ".wmv", ".flv", ".ts", ".3gp", ".mts"}
MAX_RIGHE_LOG = 4000                      # il log resta consultabile, ma non infinito

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3      # 8 GB: un video intero ci sta

_lavori: dict[str, dict] = {}
_lucchetto = threading.Lock()             # protegge _lavori
_coda = threading.Semaphore(1)            # una conversione alla volta


# -----------------------------------------------------------------------------
#  lavoro: creazione, esecuzione, stato
# -----------------------------------------------------------------------------
def _nuovo_lavoro(nome_file: str, video: Path, bpm: float, ottava: int) -> str:
    jid = uuid.uuid4().hex[:12]
    with _lucchetto:
        _lavori[jid] = {
            "id": jid,
            "nome": nome_file,
            "video": video,
            "midi": video.with_name("song.mid"),
            "bpm": bpm,
            "ottava": ottava,
            "stato": "in coda",
            "log": [],
            "note": None,
            "durata": None,
            "errore": None,
            "inizio": time.time(),
            "fine": None,
        }
    threading.Thread(target=_esegui, args=(jid,), daemon=True).start()
    return jid


def _aggiungi_log(job: dict, riga: str) -> None:
    riga = riga.rstrip("\n")
    if not riga:
        return
    job["log"].append(riga)
    if len(job["log"]) > MAX_RIGHE_LOG:
        del job["log"][: len(job["log"]) - MAX_RIGHE_LOG]


def _esegui(jid: str) -> None:
    """Lancia video2midi.py come sottoprocesso e ne raccoglie l'uscita."""
    job = _lavori[jid]
    with _coda:
        job["stato"] = "in corso"
        comando = [sys.executable, str(PROGRAMMA), str(job["video"]),
                   "-o", str(job["midi"]),
                   "--bpm", ("%g" % job["bpm"]),
                   "--ottava", str(job["ottava"])]
        try:
            proc = subprocess.Popen(comando, cwd=str(BASE), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            for riga in proc.stdout:                    # type: ignore[union-attr]
                _aggiungi_log(job, riga)
            proc.wait()
        except Exception as exc:                        # pragma: no cover
            job["errore"] = "%s: %s" % (type(exc).__name__, exc)
            job["stato"] = "errore"
            job["fine"] = time.time()
            return

        if proc.returncode != 0 or not job["midi"].is_file():
            job["stato"] = "errore"
            job["errore"] = ("video2midi.py e' uscito con codice %d" % proc.returncode
                             if proc.returncode else "il MIDI non e' stato scritto")
            _aggiungi_log(job, "ERRORE: " + job["errore"])
            job["fine"] = time.time()
            return

        # il programma stampa: "note riconosciute: 253 (durata 27.36 s)"
        testo = "\n".join(job["log"])
        m = re.search(r"note riconosciute:\s*(\d+)\s*\(durata\s*([\d.]+)\s*s\)", testo)
        if m:
            job["note"] = int(m.group(1))
            job["durata"] = float(m.group(2))
        job["stato"] = "fatto"
        job["fine"] = time.time()


def _stato(job: dict) -> dict:
    adesso = job["fine"] or time.time()
    return {
        "id": job["id"],
        "nome": job["nome"],
        "stato": job["stato"],
        "note": job["note"],
        "durata": job["durata"],
        "errore": job["errore"],
        "secondi": round(adesso - job["inizio"], 1),
        "bpm": job["bpm"],
        "ottava": job["ottava"],
        "log": job["log"][-400:],
        "scarica": "/api/midi/" + job["id"] if job["stato"] == "fatto" else None,
        "ascolta": ("/api/midi/" + job["id"] + "?ascolta=1"
                    if job["stato"] == "fatto" else None),
    }


# -----------------------------------------------------------------------------
#  rotte
# -----------------------------------------------------------------------------
@app.get("/")
def pagina():
    return PAGINA, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/logo.jpg")
def logo():
    """Logo RAEDIUS usato da header e hero della pagina."""
    f = BASE / "logo-raedius.jpg"
    if not f.is_file():
        return "", 404
    return send_file(str(f), mimetype="image/jpeg")


@app.get("/api/salute")
def salute():
    return jsonify({"ok": True, "programma": PROGRAMMA.name,
                    "esiste": PROGRAMMA.is_file()})


@app.post("/api/converti")
def converti():
    caricato = request.files.get("video")
    if caricato is None or not caricato.filename:
        return jsonify({"errore": "nessun video caricato"}), 400

    nome = os.path.basename(caricato.filename)
    est = Path(nome).suffix.lower()
    if est not in ESTENSIONI:
        return jsonify({"errore": "estensione '%s' non supportata" % (est or "?")}), 400

    try:
        bpm = float(request.form.get("bpm") or 120.0)
    except ValueError:
        return jsonify({"errore": "bpm non numerico"}), 400
    if not (20.0 <= bpm <= 400.0):
        return jsonify({"errore": "bpm fuori scala (20-400)"}), 400

    try:
        ottava = int(request.form.get("ottava") or 1)
    except ValueError:
        return jsonify({"errore": "ottava non numerica"}), 400
    if not (0 <= ottava <= 8):
        return jsonify({"errore": "ottava fuori scala (0-8)"}), 400

    cartella = LAVORI / uuid.uuid4().hex[:12]
    cartella.mkdir(parents=True, exist_ok=True)
    video = cartella / ("video" + est)
    caricato.save(str(video))

    if video.stat().st_size == 0:
        shutil.rmtree(cartella, ignore_errors=True)
        return jsonify({"errore": "il file caricato e' vuoto"}), 400

    jid = _nuovo_lavoro(nome, video, bpm, ottava)
    return jsonify({"id": jid, "stato": "in coda"})


@app.get("/api/stato/<jid>")
def stato(jid: str):
    job = _lavori.get(jid)
    if job is None:
        return jsonify({"errore": "lavoro sconosciuto"}), 404
    return jsonify(_stato(job))


@app.get("/api/midi/<jid>")
def midi(jid: str):
    job = _lavori.get(jid)
    if job is None or job["stato"] != "fatto" or not job["midi"].is_file():
        return jsonify({"errore": "MIDI non disponibile"}), 404
    scarica_come = Path(job["nome"]).stem + ".mid"
    # ?ascolta=1 -> file servito "inline": il player della pagina lo legge con fetch()
    return send_file(str(job["midi"]), mimetype="audio/midi",
                     as_attachment=not request.args.get("ascolta"),
                     download_name=scarica_come)


@app.errorhandler(413)
def troppo_grande(_e):
    return jsonify({"errore": "video troppo grande per il server"}), 413


# -----------------------------------------------------------------------------
#  pagina (HTML + CSS + JS in un solo posto: niente file da copiare in giro)
# -----------------------------------------------------------------------------
PAGINA = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video → MIDI · Raedius Productions</title>
<link rel="icon" href="/logo.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;500&amp;family=Cormorant+SC:wght@500&amp;family=Inter:wght@300;400;500&amp;display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box}
  :root{
    --bg:#0f1011; --bg-2:#17181a; --panel:#1c1d20; --panel-2:#232428;
    --line:#2c2e32; --line-2:#3d4045; --text:#f2ece4; --text-dim:#b8b2a9;
    --muted:#8a857d; --accent:#e0553c; --accent-hi:#ff7a5c;
    --accent-soft:rgba(224,85,60,.14); --ok:#6fbf73; --err:#ff7a5c;
    --serif:"Cormorant Garamond","Iowan Old Style",Georgia,"Times New Roman",serif;
    --smallcaps:"Cormorant SC","Cormorant Garamond",Georgia,serif;
    --sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --radius:2px; --wrap:1100px; --gutter:clamp(18px,4vw,48px);
    --header-h:74px; --ease:cubic-bezier(.22,.61,.36,1);
  }
  html{scroll-behavior:smooth}
  body{margin:0;overflow-x:hidden;background:radial-gradient(70% 42% at 50% 0%,rgba(224,85,60,.10),transparent 65%),var(--bg);
       color:var(--text);font:16px/1.65 var(--sans);-webkit-font-smoothing:antialiased}
  h1,h2,h3{font-family:var(--serif);font-weight:500;line-height:1.12;letter-spacing:.01em;margin:0 0 .5em}
  p{margin:0 0 1em;color:var(--text-dim)}
  a{color:inherit;text-decoration:none}
  img{max-width:100%;display:block}
  button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
  ::selection{background:var(--accent);color:#fff}
  :focus-visible{outline:2px solid var(--accent);outline-offset:3px}
  .wrap{width:100%;max-width:var(--wrap);margin:0 auto;padding-inline:var(--gutter)}
  .occhiello{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.34em;
             font-size:.78rem;color:var(--accent);margin:0 0 14px}
  .etichetta{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.22em;
             font-size:.68rem;color:var(--muted)}

  /* --- Header ------------------------------------------------------------- */
  .site-header{position:sticky;top:0;z-index:60;background:rgba(15,16,17,.88);
               backdrop-filter:saturate(140%) blur(10px);border-bottom:1px solid var(--line)}
  .site-header__inner{width:100%;max-width:var(--wrap);margin:0 auto;padding:10px var(--gutter);
                      min-height:var(--header-h);display:flex;align-items:center;
                      gap:clamp(14px,3vw,40px);flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:12px;margin-right:auto;min-width:0}
  .brand__logo{width:40px;height:40px;object-fit:cover;border-radius:50%;
               border:1px solid var(--line-2);flex:0 0 40px}
  .brand__text{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.2em;
               font-size:.92rem;line-height:1.1;color:var(--text)}
  .brand__text small{display:block;font-family:var(--sans);letter-spacing:.18em;
                     font-size:.58rem;color:var(--muted);margin-top:3px}
  .nav{display:flex;align-items:center;gap:clamp(12px,2vw,30px);flex-wrap:wrap}
  .nav a{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.2em;
         font-size:.72rem;color:var(--text-dim);padding:6px 0;border-bottom:1px solid transparent;
         transition:all .28s var(--ease)}
  .nav a:hover{color:var(--text)}
  .nav a.is-active{color:var(--accent);border-color:var(--accent)}
  .badges{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
  .pill{border:1px solid var(--line);background:var(--panel);border-radius:var(--radius);
        padding:5px 9px;font-size:.62rem;letter-spacing:.12em;text-transform:uppercase;
        color:var(--muted);white-space:nowrap}
  .pill b{color:var(--text);font-weight:500}

  /* --- Hero --------------------------------------------------------------- */
  .hero{position:relative;overflow:hidden;text-align:center;
        padding:clamp(44px,7vw,88px) 0 clamp(36px,5vw,64px);border-bottom:1px solid var(--line)}
  .hero::before{content:"";position:absolute;inset:-40% -10% auto;height:460px;pointer-events:none;
                background:radial-gradient(60% 60% at 50% 0%,var(--accent-soft),transparent 70%)}
  .hero__inner{position:relative;width:100%;max-width:var(--wrap);margin:0 auto;
               padding-inline:var(--gutter)}
  .hero__logo{width:clamp(92px,14vw,136px);height:clamp(92px,14vw,136px);object-fit:cover;
              border-radius:50%;border:1px solid var(--line-2);margin:0 auto clamp(18px,3vw,28px);
              box-shadow:0 24px 60px rgba(0,0,0,.55)}
  .hero h1{font-size:clamp(2.6rem,7vw,5.2rem);font-weight:300;text-transform:uppercase;
           letter-spacing:.06em;margin:0 0 .08em}
  .hero__tag{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.34em;
             color:var(--accent);font-size:clamp(.8rem,2vw,1rem);margin:0 0 12px}
  .hero__sub{color:var(--muted);max-width:62ch;margin:0 auto clamp(22px,4vw,34px)}
  .hero__actions{display:flex;flex-wrap:wrap;gap:14px;justify-content:center}
  .bio-riga{display:flex;flex-wrap:wrap;gap:10px 28px;justify-content:center;
            margin-top:clamp(24px,4vw,38px);padding-top:18px;border-top:1px solid var(--line)}

  /* --- Contenuto ---------------------------------------------------------- */
  main{padding:0 0 clamp(56px,8vw,96px)}
  #converti{padding-top:clamp(28px,4vw,48px)}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
        padding:clamp(18px,2.5vw,28px);box-shadow:0 18px 50px rgba(0,0,0,.18)}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:.55em;
       padding:11px 20px;border:1px solid var(--line-2);border-radius:var(--radius);
       background:transparent;color:var(--text);font-family:var(--sans);font-size:.72rem;
       letter-spacing:.18em;text-transform:uppercase;font-weight:500;cursor:pointer;
       transition:all .28s var(--ease);white-space:nowrap;text-decoration:none}
  .btn:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
  .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.primary:hover{background:var(--accent-hi);border-color:var(--accent-hi);color:#fff}
  .btn:disabled{opacity:.4;cursor:not-allowed}

  /* --- Zona di caricamento ------------------------------------------------ */
  #drop{position:relative;margin:0;padding:clamp(28px,5vw,44px) 22px;
        border:1px dashed var(--line-2);border-radius:var(--radius);background:#0a0b0c;
        text-align:center;cursor:pointer;transition:all .25s var(--ease)}
  #drop::before{content:"♪";display:grid;place-items:center;width:74px;height:74px;
        margin:0 auto 16px;border:1px solid var(--line-2);border-radius:50%;
        font-family:var(--serif);font-size:2.4rem;color:var(--accent);
        background:radial-gradient(circle at 30% 25%,#26282c,#111214)}
  #drop:hover,#drop.sopra{border-color:var(--accent);background:var(--accent-soft)}
  #drop strong{display:block;font-family:var(--serif);font-size:clamp(1.3rem,2.6vw,1.8rem);
        font-weight:500;color:var(--text);margin-bottom:6px}
  #drop span{color:var(--muted);font-size:.82rem}
  #file{margin:12px 0 0;font-size:.78rem;color:var(--accent-hi);word-break:break-all;
        letter-spacing:.02em}
  details{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);
          padding:14px 16px;margin-top:18px}
  summary{cursor:pointer;font-family:var(--smallcaps);text-transform:uppercase;
          letter-spacing:.16em;font-size:.72rem;color:var(--text-dim);transition:color .25s var(--ease)}
  summary:hover{color:var(--accent)}
  details[open] summary{color:var(--accent);margin-bottom:8px}
  details label{display:inline-flex;align-items:center;gap:8px;margin:8px 20px 0 0;
                font-size:.78rem;color:var(--muted);flex-wrap:wrap}
  input[type=number]{width:96px;padding:8px 10px;border-radius:var(--radius);
        border:1px solid var(--line);background:var(--bg);color:var(--text);
        font:inherit;font-size:.82rem}
  input[type=number]:focus{outline:none;border-color:var(--accent)}
  .riga{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:18px}
  #stato{font-size:.78rem;color:var(--muted);min-height:1.4em;letter-spacing:.02em}
  #stato.errore{color:var(--accent-hi)}

  /* --- Log e risultato ----------------------------------------------------- */
  #log{display:none;margin-top:18px;max-height:340px;overflow:auto;background:#0a0b0c;
       border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;
       font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;color:#a8a29a;
       white-space:pre-wrap}
  #esito{display:none;margin-top:18px;padding:clamp(18px,3vw,26px);
         border:1px solid rgba(224,85,60,.45);border-left:2px solid var(--accent);
         border-radius:var(--radius);background:var(--panel)}
  #esito h2{margin:0 0 6px;font-family:var(--serif);font-size:1.6rem;color:var(--text)}
  #esito p{margin:0 0 14px;color:var(--text-dim);font-size:.9rem}

  /* --- Player -------------------------------------------------------------- */
  .player{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:0 0 14px}
  #play{min-width:138px}
  .barra{flex:1 1 200px;min-width:150px;height:8px;border-radius:999px;background:var(--bg);
         border:1px solid var(--line);cursor:pointer;overflow:hidden}
  #riempimento{width:0;height:100%;background:var(--accent);transition:width .08s linear}
  #tempo{font:11.5px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);
         min-width:92px;text-align:right;letter-spacing:.04em}
  .loop{display:flex;gap:6px;align-items:center;font-size:.74rem;color:var(--text-dim);
        text-transform:uppercase;letter-spacing:.12em}
  .loop input{accent-color:var(--accent);width:15px;height:15px}
  a.scarica{display:inline-flex;align-items:center;justify-content:center;padding:11px 20px;
        border:1px solid var(--accent);border-radius:var(--radius);text-decoration:none;
        background:var(--accent);color:#fff;font-size:.72rem;letter-spacing:.18em;
        text-transform:uppercase;font-weight:500;transition:all .28s var(--ease)}
  a.scarica:hover{background:var(--accent-hi);border-color:var(--accent-hi);color:#fff}
  #avviso{display:none;margin:0 0 12px;font-size:.78rem;color:var(--accent-hi)}
  code{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);
       padding:1px 5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
       font-size:.78em;color:#d8c3b6}

  /* --- Piano roll (stile FL Studio) ---------------------------------------- */
  .roll{margin:0 0 14px;border:1px solid #0a0b0c;border-radius:var(--radius);
        background:#1b1c1e;overflow:hidden}
  .roll__bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
             padding:6px 10px;background:linear-gradient(#34373b,#26282b);
             border-bottom:1px solid #0e0f10}
  .roll__titolo{font-family:var(--smallcaps);text-transform:uppercase;
                letter-spacing:.2em;font-size:.66rem;color:#dcd6cd}
  .roll__info{font:10.5px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
              color:#8d9196;letter-spacing:.03em}
  .roll__zoom{margin-left:auto;display:flex;gap:4px}
  .roll__zoom button{min-width:30px;padding:3px 9px;border:1px solid #4a4e53;
        border-radius:2px;background:#2a2d30;color:#cfd2d5;font-size:.72rem;
        line-height:1.35;cursor:pointer;transition:all .2s var(--ease)}
  .roll__zoom button:hover{background:#3d4146;border-color:#70757b;color:#fff}
  .roll__righello{position:relative;height:18px;overflow:hidden;background:#191b1d;
                  border-bottom:1px solid #0b0c0d}
  #righelloCanvas{display:block;will-change:transform}
  .roll__vista{overflow:auto;max-height:470px;background:#17181a;
               scrollbar-color:#4a4e53 #17181a;cursor:crosshair;touch-action:none}
  #rollCanvas{display:block}
  #esito .roll__nota{margin:0;padding:5px 10px 6px;background:#141517;
        border-top:1px solid #0e0f10;font-size:.68rem;color:#7f8388;letter-spacing:.02em}

  /* --- README / documentazione -------------------------------------------- */
  #readme{scroll-margin-top:calc(var(--header-h) + 16px);padding-top:clamp(36px,5vw,60px)}
  .readme .readme__lead{max-width:82ch;font-size:.92rem;color:var(--text-dim)}
  .readme .readme__lead b{color:var(--text)}
  .readme__layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:clamp(20px,3vw,36px);
                  margin-top:22px;align-items:start}
  .readme__toc{position:sticky;top:calc(var(--header-h) + 20px);background:var(--panel-2);
               border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
  .readme__toc p{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.22em;
                 font-size:.68rem;color:var(--accent);margin:0 0 10px}
  .readme__toc ol{margin:0;padding:0 0 0 1.15em;color:var(--text-dim)}
  .readme__toc li{font-size:.76rem;line-height:1.55;margin-bottom:.36em}
  .readme__toc a{color:var(--text-dim)}
  .readme__toc a:hover{color:var(--accent)}
  .readme__body{min-width:0}
  .readme__sec{padding:0 0 22px;margin:0 0 24px;border-bottom:1px solid var(--line);
                scroll-margin-top:calc(var(--header-h) + 20px)}
  .readme__sec:last-child{border-bottom:0;margin-bottom:0;padding-bottom:0}
  .readme__sec h3{font-family:var(--smallcaps);font-size:1.08rem;font-weight:500;
                  letter-spacing:.08em;text-transform:uppercase;color:var(--text);margin:0 0 10px}
  .readme__sec h4{font-family:var(--sans);font-size:.72rem;font-weight:600;letter-spacing:.12em;
                  text-transform:uppercase;color:var(--accent);margin:18px 0 7px}
  .readme__body p,.readme__body li{font-size:.84rem;line-height:1.75;color:var(--text-dim)}
  .readme__body p{margin:0 0 .9em}
  .readme__body ul,.readme__body ol{margin:0 0 .95em 1.2em;padding:0}
  .readme__body li{margin-bottom:.3em}
  .readme__body strong{color:var(--text);font-weight:600}
  .readme__body code{background:var(--panel-2);border:1px solid var(--line);border-radius:3px;
                     padding:1px 5px;font-family:ui-monospace,monospace;font-size:.78em;
                     color:#f0c2b6}
  .readme__body pre{background:#0a0b0c;border:1px solid var(--line);border-radius:var(--radius);
                    padding:12px 14px;overflow:auto;margin:0 0 12px;color:#d8c3b6;
                    font:12px/1.6 ui-monospace,monospace}
  .readme__body pre code{background:none;border:0;padding:0;color:inherit}
  .readme__flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;
                margin:12px 0 18px}
  .readme__flow div{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--radius);
                    padding:9px 11px;font-size:.74rem;line-height:1.45;color:var(--text-dim)}
  .readme__flow b{display:block;font-family:var(--smallcaps);letter-spacing:.12em;
                  text-transform:uppercase;color:var(--accent);font-size:.66rem;margin-bottom:3px}
  .readme__note{background:var(--accent-soft);border-left:2px solid var(--accent);
                padding:10px 13px;margin:12px 0;font-size:.8rem;line-height:1.65;color:var(--text-dim)}
  .readme__note b{color:var(--text)}
  .readme__note.warn{background:rgba(224,177,92,.07);border-left-color:#e0b15c}
  .readme__body table{font-size:.78rem;margin:8px 0 14px}
  .readme__body th,.readme__body td{position:static;background:none;padding:6px 8px;
                                    border-bottom:1px solid var(--line)}
  .readme__body th{color:var(--accent);font-weight:500;white-space:nowrap}
  .readme__body td{color:var(--text-dim);vertical-align:top}
  .readme__body td code{white-space:nowrap}
  @media (max-width:900px){
    .readme__layout{grid-template-columns:1fr}
    .readme__toc{position:static}
    .readme__toc ol{columns:2;column-gap:22px}
  }
  @media (max-width:560px){
    .readme__toc ol{columns:1}
    .readme__body table{display:block;overflow-x:auto}
  }

  /* --- Footer -------------------------------------------------------------- */
  .site-footer{border-top:1px solid var(--line);background:var(--bg-2);
               padding:clamp(40px,6vw,68px) 0 24px;margin-top:clamp(48px,7vw,80px)}
  .site-footer__grid{display:grid;gap:clamp(24px,4vw,48px);
                     grid-template-columns:1.4fr 1fr 1fr;margin-bottom:32px}
  .site-footer h3{font-family:var(--smallcaps);text-transform:uppercase;letter-spacing:.16em;
                  font-size:.95rem;color:var(--text);margin:0 0 12px}
  .site-footer p{font-size:.82rem;color:var(--muted);max-width:44ch}
  .footer-list{list-style:none;padding:0;margin:0;display:grid;gap:8px}
  .footer-list li{color:var(--text-dim);font-size:.8rem;line-height:1.6}
  .footer-bottom{border-top:1px solid var(--line);padding-top:20px;display:flex;
                 flex-wrap:wrap;gap:10px 24px;justify-content:space-between;color:var(--muted);
                 font-size:.7rem;letter-spacing:.08em;text-transform:uppercase}

  #top,#converti,#readme,#guida{scroll-margin-top:calc(var(--header-h) + 16px)}
  @media (max-width:900px){
    .site-header__inner{gap:12px;padding-bottom:10px}
    .nav{order:3;width:100%;justify-content:center;gap:18px;padding-bottom:4px}
    .badges{margin-left:0}
  }
  @media (max-width:760px){
    .site-footer__grid{grid-template-columns:1fr}
    .hero__actions .btn{width:100%}
  }
</style>
</head>
<body>
<header class="site-header">
  <div class="site-header__inner">
    <a class="brand" href="#top" aria-label="Raedius Productions">
      <img class="brand__logo" src="/logo.jpg" alt="Raedius Productions">
      <span class="brand__text">Raedius Productions<small>Video → MIDI · FL Studio</small></span>
    </a>
    <nav class="nav" aria-label="Sezioni della pagina">
      <a href="#converti" class="is-active">Converti</a>
      <a href="#readme">README</a>
      <a href="#guida">Come funziona</a>
    </nav>
    <div class="badges">
      <span class="pill">Elaborazione locale</span>
      <span class="pill">Max 8 GB</span>
    </div>
  </div>
</header>

<main id="top">
<section class="hero">
  <div class="hero__inner">
    <img class="hero__logo" src="/logo.jpg" alt="Raedius Productions">
    <p class="occhiello">Raedius Productions · Tools</p>
    <h1>Video → MIDI</h1>
    <p class="hero__tag">FL Studio piano roll</p>
    <p class="hero__sub">Carica la registrazione del piano roll: esce un file <code>.mid</code> pronto per il DAW. Puoi ascoltarlo subito qui, senza installare nulla.</p>
    <div class="hero__actions">
      <a class="btn primary" href="#converti">Carica un video</a>
      <a class="btn" href="#readme">Come funziona</a>
    </div>
    <div class="bio-riga">
      <span class="etichetta">Nessun cloud</span>
      <span class="etichetta">Parser MIDI in JS</span>
      <span class="etichetta">Web Audio</span>
    </div>
  </div>
</section>

<div class="wrap" id="converti">
  <form id="modulo" class="card">
    <div id="drop">
      <strong>Trascina qui un video, o clicca per sceglierlo</strong>
      <span>mp4, mov, mkv, avi, webm · nessun limite pratico di durata</span>
      <div id="file"></div>
      <input type="file" id="video" name="video" accept="video/*" hidden>
    </div>

    <details>
      <summary>Opzioni (di solito non servono)</summary>
      <label>BPM del file scritto <input type="number" id="bpm" value="120" min="20" max="400" step="0.01"></label>
      <label>Ottava FL della riga più bassa <input type="number" id="ottava" value="1" min="0" max="8" step="1"></label>
    </details>

    <div class="riga">
      <button type="submit" id="invia" class="btn primary" disabled>Converti</button>
      <span id="stato"></span>
    </div>
  </form>

  <pre id="log"></pre>

  <section id="esito" aria-live="polite">
    <h2>Conversione finita</h2>
    <p id="riassunto"></p>
    <div class="player">
      <button type="button" id="play" class="btn" disabled>▶ Ascolta</button>
      <div class="barra" id="barra" title="clicca per spostarti nel brano">
        <div id="riempimento"></div>
      </div>
      <span id="tempo">0:00 / 0:00</span>
      <label class="loop"><input type="checkbox" id="loop">loop</label>
    </div>
    <div class="roll" id="roll">
      <div class="roll__bar">
        <span class="roll__titolo">Piano roll</span>
        <span class="roll__info" id="rollInfo"></span>
        <div class="roll__zoom" role="group" aria-label="Zoom del piano roll">
          <button type="button" id="zoomOut" title="Riduci lo zoom">−</button>
          <button type="button" id="zoomFit" title="Tutto il brano nella larghezza">Adatta</button>
          <button type="button" id="zoomIn" title="Ingrandisci">+</button>
        </div>
      </div>
      <div class="roll__righello" id="rollRighello">
        <canvas id="righelloCanvas" aria-hidden="true"></canvas>
      </div>
      <div class="roll__vista" id="rollVista">
        <canvas id="rollCanvas" aria-label="Piano roll del MIDI convertito"></canvas>
      </div>
      <p class="roll__nota">La tastiera a sinistra e le note sono quelle del MIDI: clicca o
        trascina sul piano roll per spostarti nel brano.</p>
    </div>
    <p id="avviso"></p>
    <a class="scarica" id="scarica" href="#" download>Scarica il MIDI</a>
  </section>
</div>
<!-- ================== README: COME FUNZIONA LA GRIGLIA ================== -->
<section class="wrap readme" id="readme">
  <p class="occhiello">Documentazione</p>
  <h2>README — come il piano roll diventa una griglia di note</h2>
  <p class="readme__lead">Questa pagina documenta il funzionamento di <code>video2midi.py</code>, il
    programma che trasforma la registrazione di un <b>piano roll di FL Studio</b> in un file MIDI.
    Non c'è una rete neurale: il lavoro è tutto di <b>computer vision</b>. La tastiera disegnata a
    sinistra dà le <b>righe dei semitoni</b>; la testina del playhead e lo scorrimento della griglia
    danno il <b>tempo</b>; le testine colorate delle note danno <b>inizio e fine</b> di ogni nota.</p>

  <div class="readme__layout">
    <nav class="readme__toc" aria-label="Indice del README">
      <p>Indice</p>
      <ol>
        <li><a href="#readme-modello">Che cos'è la griglia</a></li>
        <li><a href="#readme-io">Cosa entra e cosa esce</a></li>
        <li><a href="#readme-zone">1. Tastiera e inizio del roll</a></li>
        <li><a href="#readme-righe">2. Le righe dei semitoni</a></li>
        <li><a href="#readme-ottava">3. Nomi e ottava</a></li>
        <li><a href="#readme-tempo">4. Dal pixel al tempo</a></li>
        <li><a href="#readme-note">5. Le note in un fotogramma</a></li>
        <li><a href="#readme-gruppi">6. Dagli avvistamenti alle note</a></li>
        <li><a href="#readme-errori">7. Errori tipici e rimedi</a></li>
        <li><a href="#readme-riprese">8. Come registrare il piano roll</a></li>
      </ol>
    </nav>

    <div class="readme__body">
      <article class="readme__sec" id="readme-modello">
        <h3>Che cos'è la griglia</h3>
        <p>Nel piano roll di FL Studio ogni <b>riga orizzontale</b> è un semitono e ogni <b>colonna</b>
          è un istante musicale. La tastiera a sinistra non serve a suonare: serve a sapere <i>quale</i>
          semitono è ogni riga. Se la tastiera dice che una riga è un Do, la riga sopra è Do# e quella
          sotto è Si; le righe sono equidistanti e i tasti neri formano il disegno caratteristico
          <b>2 + 3</b> (due neri, poi tre neri) che permette di riconoscere la classe di altezza.</p>
        <p>La seconda griglia è quella del <b>tempo</b>: la testina verde del playhead scorre verso
          destra, oppure resta ferma mentre il piano roll scorre sotto di lei. In entrambi i casi il
          programma misura i <b>pixel di schermo per secondo di musica</b> (<code>ppt</code>) e usa
          la relazione:</p>
        <pre><code>T = t + (x - xp(t)) / ppt</code></pre>
        <p><code>t</code> è il tempo del fotogramma, <code>xp(t)</code> la posizione della testina in
          quel fotogramma, <code>x</code> la colonna di un evento. La stessa formula vale sia quando
          la testina si muove sia quando scorre la griglia: cambia solo quale dei due termini è
          fermo.</p>
        <div class="readme__flow">
          <div><b>1 · zone</b>tastiera a sinistra, inizio del roll in alto</div>
          <div><b>2 · righe</b>tasti neri → semitoni → nota di riferimento</div>
          <div><b>3 · nomi</b>etichette OCR oppure ottava indicata a mano</div>
          <div><b>4 · tempo</b>testina e scorrimento → pixel al secondo</div>
          <div><b>5 · note</b>testine colorate → inizio, fine, altezza</div>
        </div>
        <div class="readme__note"><b>In una frase.</b> La griglia delle righe risponde a
          <i>quale nota</i>; la griglia del tempo risponde a <i>quando</i>. Le due misure sono
          indipendenti, e il programma le controlla separatamente prima di scrivere il MIDI.</div>
      </article>

      <article class="readme__sec" id="readme-io">
        <h3>Cosa entra e cosa esce</h3>
        <table>
          <thead><tr><th>Ingresso</th><th>Uso</th></tr></thead>
          <tbody>
            <tr><td>Video del piano roll</td><td>Registrazione dello schermo di FL Studio (mp4, mov,
              mkv, webm…). Deve mostrare la tastiera a sinistra e la griglia delle note.</td></tr>
            <tr><td>Ottava FL</td><td>Serve solo se le etichette della tastiera non si leggono:
              è l'ottava della riga più in basso, trattata come un Do.</td></tr>
            <tr><td>BPM</td><td>Non cambia il tempo musicale delle note: cambia solo la griglia di
              tick con cui il MIDI viene scritto. 120 va bene per la maggior parte dei casi.</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>Uscita</th><th>Contenuto</th></tr></thead>
          <tbody>
            <tr><td><code>song.mid</code></td><td>MIDI tipo 0, 480 tick per battuta, con le note
              rilevate e il tempo indicato.</td></tr>
            <tr><td>Log</td><td>Righe leggibili: zona della tastiera, passo del semitono, origine
              dell'ottava, velocità in pixel/secondo, osservazioni e note finali.</td></tr>
            <tr><td><code>debug_griglia.png</code></td><td>Solo con <code>--debug</code>: la griglia
              disegnata sul primo fotogramma, con le righe dei Do etichettate.</td></tr>
          </tbody>
        </table>
        <p>La pagina web è un'interfaccia sopra lo stesso programma: carica il video, lo salva in
          <code>web_lavori/</code>, lancia <code>video2midi.py</code> come sottoprocesso e mostra il
          log in tempo reale. Alla fine il MIDI si può scaricare, ascoltare nel browser con il parser
          SMF e la Web Audio API e vedere in un <b>piano roll in stile FL Studio</b>: la tastiera a
          sinistra, le righe dei semitoni, le note verdi, la testina che avanza e i tasti che si
          accendono durante la riproduzione. I pulsanti <b>−</b>, <b>+</b> e <b>Adatta</b> cambiano
          lo zoom; un clic (o un trascinamento) sul piano roll sposta il punto di ascolto.</p>
      </article>
      <article class="readme__sec" id="readme-zone">
        <h3>1. Tastiera e inizio del roll</h3>
        <p>Prima di leggere le note, il programma deve capire <b>dove finisce la tastiera</b> e
          <b>dove comincia il piano roll</b>.</p>
        <ul>
          <li><b>La tastiera</b> è la fascia chiara a sinistra. Si calcola la luminosità mediana di
            ogni colonna sotto la barra strumenti e si cerca l'ultima colonna chiara entro il 35%
            della larghezza: quella è <code>kb_end</code>, il confine fra tastiera e note.</li>
          <li><b>L'inizio del roll</b> è <code>y_top</code>. La sorgente più affidabile è la testina
            del playhead: è una linea verticale che parte dal bordo alto della griglia. Il programma
            campiona sei fotogrammi distribuiti nel video, trova la colonna della testina e prende la
            sua <b>riga più alta</b>; la mediana dei campioni è l'inizio del roll. Se la testina non si
            vede, usa il ripiego: la prima fascia scura lunga sotto la barra strumenti.</li>
        </ul>
        <p>Queste due coordinate non sono un dettaglio: se <code>kb_end</code> è troppo a destra si
          tagliano le note, se <code>y_top</code> è troppo in alto si includono la barra e i menu di
          FL Studio, se è troppo in basso si perdono le prime righe.</p>
      </article>

      <article class="readme__sec" id="readme-righe">
        <h3>2. Le righe dei semitoni</h3>
        <p>La tastiera disegnata a sinistra è la fonte della griglia verticale. I tasti neri sono
          righe scure; i bianchi sono righe chiare. Il programma costruisce un <b>profilo verticale
          di luminosità</b> mediando le colonne della tastiera (o, se la tastiera non basta, una
          striscia centrale del roll), trova le righe più scure e ne prende i centri. Ogni gruppo di
          tasti neri viene così ridotto a una serie di quote <code>y</code>.</p>
        <p>Da qui si stima il passo del semitono:</p>
        <ol>
          <li>si ordinano le quote dei neri e si misurano i salti fra una e la successiva;</li>
          <li>il salto minimo vale almeno <b>due semitoni</b> (fra due neri consecutivi c'è sempre
            almeno un bianco), quindi <code>h₀ = salto_minimo / 2</code> è la prima stima
            dell'altezza di una riga;</li>
          <li>ogni salto viene arrotondato a un numero intero di semitoni e si costruisce una scala
            cumulativa di offset;</li>
          <li>una <b>retta ai minimi quadrati</b> <code>y = y_rif − h · offset</code> trova la
            posizione <code>y_rif</code> della riga di riferimento e l'altezza definitiva
            <code>h</code>;</li>
          <li><code>h</code> deve stare fra 3 e 60 pixel: fuori da lì la griglia non è credibile.</li>
        </ol>
        <p>Infine si riconosce la <b>classe di altezza</b> della riga di riferimento. Le righe dei
          neri devono corrispondere al disegno dei tasti neri (classi 1, 3, 6, 8, 10). Il programma
          prova le dodici possibili traslazioni e tiene quella che spiega esattamente quel disegno:
          è il passaggio che distingue «una riga» da «un Do».</p>
        <div class="readme__note warn"><b>Perché serve il disegno 2 + 3.</b> Le righe sono tutte
          uguali: senza il motivo dei tasti neri il programma non saprebbe se una riga è un Do o un
          Re. La periodicità del pianoforte è l'unico appiglio, e va letta su più neri insieme, non
          su uno solo.</div>
      </article>
      <article class="readme__sec" id="readme-ottava">
        <h3>3. Nomi e ottava</h3>
        <p>La griglia sa che una riga è un Do, ma non <i>quale</i> Do. Per l'ottava il programma usa
          due strade, in ordine di preferenza.</p>
        <h4>Le etichette della tastiera (OCR)</h4>
        <p>Nella tastiera di FL Studio compaiono etichette come <code>C5</code>, <code>F4</code>,
          <code>A#3</code>. Il programma ritaglia la striscia a destra dei tasti, la ingrandisce otto
          volte, la binarizza con Otsu e la passa a <b>tesseract</b> in due modalità (testo sparso e
          blocco unico). Tiene le parole con confidenza almeno 35, normalizza i caratteri ambigui
          (<code>£6</code> → <code>F6</code>, <code>BS</code> → <code>B5</code>, <code>O</code> →
          <code>0</code>…) e converte ogni etichetta nella coppia <code>(y, midi)</code>.</p>
        <p>Poi le etichette <b>votano</b> la riga di riferimento: per ogni etichetta si calcola quanti
          semitoni dista dalla riga <code>y_rif</code>; se la classe di altezza che ne esce coincide
          con <code>pc_rif</code>, l'etichetta propone un MIDI per quella riga. La mediana dei voti è
          l'ancora. Un'etichetta isolata sbagliata non sposta la griglia; se non c'è nessun voto
          coerente, si passa alla seconda strada.</p>
        <h4>L'ottava indicata a mano</h4>
        <p>Se l'OCR non legge, si usa l'ottava FL scelta nella pagina (o <code>--ottava</code>). La
          riga più in basso del fotogramma viene trattata come un Do e il programma calcola il MIDI
          della riga di riferimento di conseguenza. Attenzione alla convenzione: <b>FL Studio chiama
          C5 il Do centrale (MIDI 60)</b>, mentre il file MIDI esce nella convenzione standard
          (<b>C4 = MIDI 60</b>). Una nota «F4» di FL Studio esce quindi come F3: non è un errore, è
          il cambio di convenzione.</p>
        <p>Con l'ancora pronta, le righe vengono generate dal basso all'alto:
          <code>midi = riferimento + j</code>, <code>y = y_rif − h · j</code>. Si tengono solo le
          righe dentro il fotogramma e con MIDI fra 0 e 127. Il risultato è l'elenco
          <code>(y, midi)</code> che verrà usato per leggere le note.</p>
        <div class="readme__note warn"><b>Se le altezze sono tutte spostate.</b> Il primo sospetto è
          l'ottava, non la griglia: guarda il log («ottava: ricavata da N etichette» oppure «uso
          --ottava»), controlla <code>debug_griglia.png</code> e, se serve, indica l'ottava FL
          corretta. Il BPM non c'entra.</div>
      </article>

      <article class="readme__sec" id="readme-tempo">
        <h3>4. Dal pixel al tempo</h3>
        <p>Il tempo non si legge dall'orologio del video: si legge dal movimento della griglia. Il
          programma cerca la <b>testina</b> in ogni fotogramma campionato con una maschera HSV
          (verde/ciano: tonalità 36–62, saturazione &gt; 110, luminosità 34–135), esclude la zona
          della tastiera e prende la colonna più “accesa”. Una colonna è valida solo se la testina è
          alta almeno un quarto del roll: un riflesso verde isolato non conta.</p>
        <p>Poi misura i <b>pixel di schermo per secondo di musica</b> in due modi:</p>
        <ol>
          <li><b>Scorrimento della griglia</b> (preferito): confronta due fotogrammi distanti circa
            mezzo secondo con la correlazione di fase. La colonna della testina viene cancellata,
            perché è ferma e tirerebbe la misura verso zero. Si tiene la mediana delle misure
            affidabili (correlazione ≥ 0,30 e spostamento ≥ 2 px). Questo funziona anche quando FL
            Studio fa auto-scroll e la testina resta ferma.</li>
          <li><b>Rampa della testina</b> (ripiego): confronta la posizione della testina fra
            fotogrammi distanti e prende la mediana di <code>Δx / Δt</code>. Serve quando la griglia
            non scorre ma la testina avanza.</li>
        </ol>
        <p>Se nessuno dei due metodi trova un movimento credibile, il programma si ferma con
          «non capisco come scorre il tempo in questo video». È un errore onesto: senza sapere quanto
          vale un pixel in secondi, le note non si possono collocare.</p>
        <p>Con <code>ppt</code> e la posizione <code>xp(t)</code> della testina, ogni colonna
          <code>x</code> diventa un tempo con la formula vista sopra. Una nota tagliata dal bordo
          sinistro viene <b>saltata</b>: non se ne conosce l'inizio. Una nota che arriva al bordo
          destro o passa sotto la testina non ha una fine misurabile in quel fotogramma: la fine verrà
          stimata dagli avvistamenti successivi o dal raggruppamento.</p>
      </article>
      <article class="readme__sec" id="readme-note">
        <h3>5. Le note in un fotogramma</h3>
        <p>Per ogni riga della griglia e per ogni fotogramma campionato, il programma guarda una
          <b>banda orizzontale</b> attorno alla quota <code>y</code> del semitono. Lì dentro separa
          tre cose:</p>
        <ul>
          <li>il <b>fondo</b> della griglia, stimato come mediana della banda <i>escludendo</i> la
            zona della testina: così il tema scuro di FL Studio non viene scambiato per una nota;</li>
          <li>le <b>testine</b> delle note, cioè i pixel chiari che spiccano sul fondo
            (<code>diff &gt; 55</code> e luminosità <code>&gt; 110</code>);</li>
          <li>il <b>corpo</b> della nota, meno luminoso ma più esteso
            (<code>diff &gt; 9</code> e luminosità <code>&gt; 45</code>).</li>
        </ul>
        <p>I pixel vengono ridotti a intervalli orizzontali; i buchi piccoli si chiudono, perché una
          nota può essere interrotta da una linea della griglia. Poi si cercano le <b>testine dentro
          il corpo</b>: ogni testina è una nota che comincia, il corpo dice fin dove arriva. Se una
          testina è seguita da un'altra testina, la prima nota finisce poco prima della seconda. Una
          testina al bordo sinistro viene scartata; una che tocca il bordo destro o la testina del
          playhead lascia la fine «da stimare».</p>
        <p>Il risultato è un'<b>osservazione</b>: <code>(midi, t_inizio, t_fine)</code>. Lo stesso
          evento viene osservato in più fotogrammi: è proprio questa ridondanza che permette di
          distinguere una nota vera da un lampeggio o da un riflesso.</p>
        <div class="readme__note"><b>Perché soglie relative al fondo.</b> Il colore delle note e il
          tema di FL Studio possono cambiare. Il programma non cerca un colore assoluto: misura
          quanto una colonna è più chiara del fondo <i>in quel fotogramma</i>, quindi funziona anche
          con temi diversi.</div>
      </article>

      <article class="readme__sec" id="readme-gruppi">
        <h3>6. Dagli avvistamenti alle note</h3>
        <p>Le osservazioni vengono raggruppate per altezza e ordinate nel tempo. Due osservazioni
          della stessa nota separate da meno di <b>0,09 s</b> appartengono allo stesso gruppo; un
          silenzio più lungo apre un nuovo gruppo. Un gruppo con <b>una sola osservazione</b> viene
          scartato come rumore: una nota vera si vede in più fotogrammi.</p>
        <p>Per ogni gruppo:</p>
        <ul>
          <li>l'inizio è la <b>mediana</b> degli inizi osservati;</li>
          <li>la fine è la mediana delle fini misurate, se ce ne sono almeno due e sono
            successivi all'inizio; altrimenti si usa un valore minimo di 0,12 s;</li>
          <li>una durata superiore a <b>2 s</b> viene tagliata a 2 s: oltre, il gruppo è più
            probabilmente una nota tenuta o un corpo lungo letto male che una nota vera;</li>
          <li>all'inizio del brano si sottrae il tempo della prima nota, così il MIDI parte da zero:
            il silenzio iniziale del video non viene conservato.</li>
        </ul>
        <p>Il file viene scritto come MIDI tipo 0, con risoluzione di 480 tick per battuta e un
          evento di tempo iniziale. Il <b>BPM</b> scelto cambia solo la corrispondenza fra secondi e
          tick: la musica suona alla stessa velocità, cambia la griglia del DAW. Se il DAW deve
          mostrare le stesse battute di FL Studio, usa lo stesso BPM del progetto originale.</p>
        <h4>Riga di comando</h4>
        <pre><code># conversione semplice: crea video.mid accanto al video
python3 video2midi.py video.mp4

# file di uscita, ottava di ripiego e immagini di controllo
python3 video2midi.py video.mp4 -o song.mid --ottava 4 --bpm 120 --debug debug_fl/</code></pre>
        <p>Opzioni utili: <code>-o</code> per il nome del MIDI, <code>--ottava</code> se l'OCR non
          legge le etichette, <code>--bpm</code> per la griglia di tick, <code>--debug</code> per
          salvare <code>debug_griglia.png</code> e le immagini della tastiera, <code>-q</code> per un
          log più silenzioso.</p>
      </article>
      <article class="readme__sec" id="readme-errori">
        <h3>7. Errori tipici e rimedi</h3>
        <table>
          <thead><tr><th>Sintomo</th><th>Causa probabile</th><th>Rimedio</th></tr></thead>
          <tbody>
            <tr><td>«griglia delle note non trovata: il video non sembra un piano roll di FL
              Studio».</td>
              <td>La tastiera non è visibile, è troppo scura o il video non è un piano roll.</td>
              <td>Registra la finestra di FL Studio con il piano roll e la tastiera a sinistra;
              controlla che la barra strumenti non copra le prime righe.</td></tr>
            <tr><td>«non capisco come scorre il tempo in questo video (testina e griglia ferme)».</td>
              <td>Il video è fermo, il playhead non si vede o la riproduzione non è partita.</td>
              <td>Registra mentre il brano suona; lascia visibile la testina verde; non fermare il
              video su un fotogramma. Se FL non mostra la testina, usa una vista con il playhead.</td></tr>
            <tr><td>0 note, ma la griglia viene trovata.</td>
              <td>Nessuna nota nel brano, note troppo piccole, tema con poco contrasto o fotogrammi
              campionati troppo radi.</td>
              <td>Registra a 1080p o superiore, senza rimpicciolire il piano roll; controlla
              <code>debug_griglia.png</code>; usa un tema con note chiare.</td></tr>
            <tr><td>Tutte le altezze sono spostate di una o più ottave.</td>
              <td>L'OCR non ha letto le etichette e l'ottava di ripiego è sbagliata.</td>
              <td>Guarda il log: se dice «uso --ottava», indica l'ottava FL della riga più in basso.
              Ricorda che FL C5 = MIDI 60 e l'uscita è C4 = MIDI 60.</td></tr>
            <tr><td>Note in ritardo o in anticipo rispetto al video.</td>
              <td>La testina è stata persa per qualche fotogramma, oppure lo scorrimento è stato
              misurato su una zona distorta.</td>
              <td>Mantieni la testina visibile e non coperta da menu; evita registrazioni con il
              piano roll tagliato; registra a fps costanti. Il BPM non corregge questo errore.</td></tr>
            <tr><td>Mancano note o intere righe.</td>
              <td>La tastiera è tagliata, il piano roll è troppo zoomato, oppure le note sono molto
              vicine e si fondono.</td>
              <td>Inquadra la tastiera intera e un buon numero di righe; non tagliare il bordo
              sinistro; registra a risoluzione piena.</td></tr>
            <tr><td>Note troppo lunghe (fino a 2 s) o fuse fra loro.</td>
              <td>Il corpo della nota è stato letto fino alla nota successiva; il limite di 2 s è
              stato raggiunto.</td>
              <td>Riduci lo zoom o aumenta la larghezza del piano roll: più pixel per secondo
              separano meglio le testine. Evita note sovrapposte nella stessa riga.</td></tr>
            <tr><td>La prima nota del brano manca.</td>
              <td>Era già iniziata a sinistra dello schermo: il programma salta le note tagliate dal
              bordo perché non ne conosce l'inizio.</td>
              <td>Registra dall'inizio della riproduzione, con un piccolo margine prima della prima
              nota, così ogni nota entra da destra.</td></tr>
            <tr><td>Il MIDI nel DAW è più «stretto» o più «largo».</td>
              <td>Il BPM di uscita non coincide con quello del progetto FL.</td>
              <td>Imposta lo stesso BPM del progetto: cambia solo la griglia di tick, non la velocità
              di riproduzione.</td></tr>
            <tr><td>La pagina web dice «conversione finita» ma il player non parte.</td>
              <td>Il MIDI è vuoto o il browser non ha inizializzato l'audio.</td>
              <td>Controlla il numero di note nel riassunto e il log; premi di nuovo ▶ dopo il primo
              clic (i browser richiedono un gesto utente per l'audio).</td></tr>
          </tbody>
        </table>
        <div class="readme__note warn"><b>Il video deve mostrare il tempo.</b> Se il piano roll è
          fermo e la testina non si muove, non esiste alcuna informazione per sapere quando avviene
          una nota. Il programma non inventa il tempo: si ferma e lo dice. Registra la riproduzione,
          non una schermata statica.</div>
      </article>
      <article class="readme__sec" id="readme-riprese">
        <h3>8. Come registrare il piano roll</h3>
        <p>La conversione è affidabile quanto lo è la registrazione. Il programma non ha bisogno di
          un video bello: ha bisogno di un video <b>leggibile e con il tempo che scorre</b>.</p>
        <ul>
          <li><b>Registra lo schermo, non la webcam.</b> Il piano roll deve essere nitido: 1080p è
            il minimo comodo, 1440p o 4K aiutano sulle note piccole.</li>
          <li><b>Frame rate costante.</b> 30 o 60 fps vanno bene; evita registrazioni a frame rate
            variabile o con frame duplicati pesanti.</li>
          <li><b>Finestra FL Studio massimizzata</b> e piano roll largo. La tastiera a sinistra deve
            restare visibile con le sue etichette: è la fonte dell'ottava.</li>
          <li><b>Testina visibile</b> e non coperta da menu, popup o dalla finestra del browser.
            Se il playhead è nascosto, il tempo non si può misurare.</li>
          <li><b>Riproduzione dall'inizio</b>, con qualche istante di margine prima della prima
            nota: le note che entrano da destra si leggono meglio di quelle già iniziate a sinistra.</li>
          <li><b>Non cambiare zoom, scroll o dimensione della finestra</b> durante la registrazione:
            un salto di scala confonde la griglia delle righe e la misura del tempo.</li>
          <li><b>Niente overlay</b>: notifiche, cursore sopra le note, didascalie o registrazioni
            con bordi neri che tagliano il piano roll.</li>
          <li><b>Tema leggibile</b>: note chiare su fondo scuro o viceversa, senza riflessi o
            trasparenze che cancellino le testine.</li>
        </ul>
        <h4>Checklist prima di convertire</h4>
        <ol>
          <li>Nel video si vede la tastiera con le etichette?</li>
          <li>La testina verde (o lo scorrimento della griglia) è visibile per tutto il brano?</li>
          <li>Il piano roll non è tagliato a sinistra o a destra?</li>
          <li>Le note sono abbastanza grandi da distinguere testina e corpo?</li>
          <li>Hai usato un frame rate costante e una risoluzione piena?</li>
          <li>Se qualcosa non torna, hai lanciato con <code>--debug</code> e guardato
            <code>debug_griglia.png</code>?</li>
        </ol>
        <p>Quando la griglia delle righe e quella del tempo sono entrambe leggibili, il resto è
          contabilità: le testine diventano osservazioni, le osservazioni diventano note, le note
          diventano un MIDI. Se una delle due griglie non è misurabile, il programma lo dice nel log
          invece di produrre un file pieno di note inventate.</p>
      </article>
    </div><!-- /readme__body -->
  </div><!-- /readme__layout -->
</section>
<!-- /README -->
</main>

<footer class="site-footer" id="guida">
  <div class="wrap">
    <div class="site-footer__grid">
      <div>
        <p class="occhiello">Raedius Productions</p>
        <h3>Video → MIDI</h3>
        <p>Conversione locale delle registrazioni del piano roll di FL Studio in file MIDI,
          con ascolto immediato nel browser.</p>
      </div>
      <div>
        <p class="occhiello">Come legge il video</p>
        <ul class="footer-list">
          <li><a href="#readme">README — come funziona la griglia</a></li>
          <li>usa le etichette della tastiera per l'ottava (se non le legge: quella scritta sopra);</li>
          <li>se il video è <em>piano roll di FL Studio</em>, con la testina verde o la griglia che scorre;</li>
          <li>le note già uscite a sinistra dello schermo vengono saltate;</li>
          <li>il MIDI parte da zero: l'eventuale silenzio iniziale del video non c'è.</li>
        </ul>
      </div>
      <div>
        <p class="occhiello">Ascolto</p>
        <p>Il pulsante <strong>▶ Ascolta</strong> suona il MIDI qui nel browser (Web Audio):
          puoi mettere in pausa, cliccare la barra per spostarti e attivare il <em>loop</em>.
          Sotto il player compare il <strong>piano roll in stile FL Studio</strong>: le note verdi
          sono quelle del MIDI e la testina che avanza è sincronizzata con l'ascolto.</p>
      </div>
    </div>
    <div class="footer-bottom">
      <span>© 2026 Raedius Productions</span>
      <span>Video → MIDI · FL Studio</span>
    </div>
  </div>
</footer>
<script>
const drop = document.getElementById('drop');
const input = document.getElementById('video');
const fileBox = document.getElementById('file');
const modulo = document.getElementById('modulo');
const invia = document.getElementById('invia');
const statoEl = document.getElementById('stato');
const logEl = document.getElementById('log');
const esito = document.getElementById('esito');
const riassunto = document.getElementById('riassunto');
const scarica = document.getElementById('scarica');
const playBtn = document.getElementById('play');
const barra = document.getElementById('barra');
const riempimento = document.getElementById('riempimento');
const tempoEl = document.getElementById('tempo');
const loopChk = document.getElementById('loop');
const avviso = document.getElementById('avviso');
let scelto = null;
let timer = null;

function mb(n){ return n > 1048576 ? (n/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(n/1024))+' KB'; }
function stato(testo, errore){
  statoEl.textContent = testo || '';
  statoEl.classList.toggle('errore', !!errore);
}

function scegli(file){
  if(!file) return;
  if(!/^video\//.test(file.type) && !/\.(mp4|mov|mkv|avi|webm|m4v|mpg|mpeg|wmv|flv|ts|3gp|mts)$/i.test(file.name)){
    stato('Questo file non sembra un video.', true);
    return;
  }
  scelto = file;
  fileBox.textContent = file.name + ' · ' + mb(file.size);
  invia.disabled = false;
  fermaPlayback();                 // niente audio del brano precedente
  esito.style.display = 'none';
  logEl.style.display = 'none';
  logEl.textContent = '';
  stato('');
}

drop.addEventListener('click', () => input.click());
input.addEventListener('change', () => scegli(input.files[0]));
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.add('sopra');
}));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.remove('sopra');
}));
drop.addEventListener('drop', ev => scegli(ev.dataTransfer.files[0]));

modulo.addEventListener('submit', async ev => {
  ev.preventDefault();
  if(!scelto || timer) return;

  const dati = new FormData();
  dati.append('video', scelto);
  dati.append('bpm', document.getElementById('bpm').value || '120');
  dati.append('ottava', document.getElementById('ottava').value || '1');

  invia.disabled = true;
  fermaPlayback();
  esito.style.display = 'none';
  logEl.style.display = 'block';
  logEl.textContent = 'Caricamento del video…';
  stato('Caricamento…');

  try {
    const r = await fetch('/api/converti', {method:'POST', body:dati});
    const d = await r.json();
    if(!r.ok) throw new Error(d.errore || ('errore HTTP ' + r.status));
    stato('In coda…');
    timer = setInterval(() => attendi(d.id), 800);
    attendi(d.id);
  } catch (e) {
    stato(e.message, true);
    invia.disabled = false;
  }
});

function chiudi(){
  clearInterval(timer);
  timer = null;
  invia.disabled = false;
}

async function attendi(id){
  let d;
  try {
    const r = await fetch('/api/stato/' + id);
    d = await r.json();
    if(!r.ok) throw new Error(d.errore || ('errore HTTP ' + r.status));
  } catch (e) {
    stato(e.message, true);
    chiudi();
    return;
  }

  if(Array.isArray(d.log)){
    logEl.textContent = d.log.join('\n');
    logEl.scrollTop = logEl.scrollHeight;
  }

  if(d.stato === 'in coda'){
    stato('In coda…');
  } else if(d.stato === 'in corso'){
    stato('Conversione in corso… ' + d.secondi + ' s');
  } else if(d.stato === 'fatto'){
    chiudi();
    stato('Fatto in ' + d.secondi + ' s.');
    const note = (d.note === null ? '?' : d.note);
    const durata = (d.durata === null ? '?' : d.durata.toFixed(2));
    riassunto.textContent = note + ' note · ' + durata + ' secondi di musica';
    scarica.href = d.scarica;
    scarica.setAttribute('download', (d.nome || 'video').replace(/\.[^.]+$/, '') + '.mid');
    bpmBrano = Number(d.bpm) || 120;   // il piano roll disegna le battute su questo tempo
    caricaMidi(d.id);
    esito.style.display = 'block';
    esito.scrollIntoView({behavior:'smooth', block:'nearest'});
  } else if(d.stato === 'errore'){
    chiudi();
    stato(d.errore || 'La conversione non è riuscita.', true);
    logEl.style.display = 'block';
  }
}
</script>
<script>
// ===================== player MIDI: parser SMF + sintesi Web Audio =====================
// Stesso approccio dello Studio di pianolab: nessuna libreria, nessun file audio,
// il .mid viene letto qui nel browser e suonato con oscillatori (triangle + sine).
let evSuonate = [];            // {note, velocity, start, duration, end}
let durataBrano = 0;
let posCorrente = 0;           // secondo da cui parte / riparte il play
let audioCtx = null, master = null;
let attivi = new Set();        // oscillatori programmati, da spegnere su pausa/stop
let inRiproduzione = false, tOrigine = 0, raf = null, prossima = 0;
const FINESTRA = 1.5;          // secondi di note programmate in anticipo

function mmss(s){
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function aggiornaBarra(pos){
  const d = durataBrano || 0;
  riempimento.style.width = (d ? Math.min(100, Math.max(0, pos / d * 100)) : 0) + '%';
  tempoEl.textContent = mmss(pos) + ' / ' + mmss(d);
}

// ===================== piano roll in stile FL Studio =====================
// Tastiera a sinistra, righe dei semitoni, griglia di battute e note verdi:
// tutto disegnato su canvas, nessuna immagine e nessuna libreria. Lo sfondo
// (tastiera + griglia + note) si costruisce una volta sola in un canvas fuori
// schermo; a ogni fotogramma si sovrappongono solo le note accese e la testina,
// così il disegno resta leggero anche sui brani lunghi.
const TASTIERA = 76;          // larghezza della tastiera, in pixel
const ALTEZZARIGHELLO = 18;   // altezza della striscia fissa con le battute
const RIGHEVOLO = 0;          // il righello ora è fuori dal canvas che scorre
const RIGA = 12;              // altezza di un semitono
const TONO_NOTA = 92;         // tonalità HSL delle note: il verde di FL Studio
const LARGHEZZA_MAX = 12000;  // oltre questa larghezza il canvas diventa pesante

let bpmBrano = 120;
let zoomRoll = 1;             // 1 = tutto il brano nella larghezza della vista
let rollPronto = false;
let rollDisp = null;          // misure correnti del disegno
let rollSfondo = document.createElement('canvas');
let rollRighello = document.createElement('canvas');
let trascinoRoll = false;
let riprendiDopoTrascino = false;

const NOMI_NOTE = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
function nomeNota(midi){ return NOMI_NOTE[midi % 12] + Math.floor(midi / 12); } // FL: 60 = C5
function tastoNero(midi){
  const c = midi % 12;
  return c === 1 || c === 3 || c === 6 || c === 8 || c === 10;
}

function percorsoArrotondato(ctx, x, y, w, h, r){
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// --- misure del disegno: righe visibili, scala del tempo, dimensione canvas ---
function preparaRoll(){
  const vista = document.getElementById('rollVista');
  const cv = document.getElementById('rollCanvas');
  const info = document.getElementById('rollInfo');
  if (!evSuonate.length){ rollPronto = false; return; }

  let bassa = 127, alta = 0;
  for (const n of evSuonate){
    if (n.note < bassa) bassa = n.note;
    if (n.note > alta) alta = n.note;
  }

  // almeno due ottave di righe: una melodia sola resta leggibile
  let min = Math.floor((bassa - 2) / 12) * 12;
  let max = Math.ceil((alta + 2) / 12) * 12 - 1;
  if (max - min + 1 < 25){
    const centro = (bassa + alta) / 2;
    min = Math.floor((centro - 12) / 12) * 12;
    max = min + 24;
  }
  min = Math.max(0, min);
  max = Math.min(127, max);

  const righe = max - min + 1;
  const altezza = RIGHEVOLO + righe * RIGA;
  const dpr = Math.min(2, window.devicePixelRatio || 1);

  // l'altezza va impostata prima di misurare: se serve la barra verticale,
  // la larghezza disponibile si riduce e il brano deve restare adattato
  cv.height = Math.round(altezza * dpr);
  cv.style.height = altezza + 'px';

  const disponibile = Math.max(300, (vista.clientWidth || 900) - 2);
  const durata = Math.max(0.5, durataBrano);
  const pxAdatta = Math.max(6, (disponibile - TASTIERA - 16) / durata);
  const pxMassimo = (LARGHEZZA_MAX - TASTIERA - 12) / durata;
  const pxSecondo = Math.min(pxAdatta * zoomRoll, pxMassimo);
  const larghezza = Math.ceil(TASTIERA + durata * pxSecondo + 12);

  cv.width = Math.round(larghezza * dpr);
  cv.style.width = larghezza + 'px';

  rollDisp = {min, max, righe, altezza, larghezza, pxSecondo, dpr,
              battito: 60 / (bpmBrano || 120)};
  rollPronto = true;
  if (info)
    info.textContent = nomeNota(bassa) + '–' + nomeNota(alta) + ' · ' +
                       evSuonate.length + ' note · ' + Math.round(bpmBrano) + ' BPM';

  const rg = document.getElementById('righelloCanvas');
  rg.width = Math.round(larghezza * dpr);
  rg.height = Math.round(ALTEZZARIGHELLO * dpr);
  rg.style.width = larghezza + 'px';
  rg.style.height = ALTEZZARIGHELLO + 'px';

  costruisciRighello();
  costruisciSfondo();
}

// --- righello fisso in alto: numeri delle battute, tacche dei battiti, segno FL ---
function costruisciRighello(){
  const d = rollDisp, s = rollRighello;
  s.width = Math.round(d.larghezza * d.dpr);
  s.height = Math.round(ALTEZZARIGHELLO * d.dpr);
  const ctx = s.getContext('2d');
  ctx.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  ctx.fillStyle = '#191b1d';
  ctx.fillRect(0, 0, d.larghezza, ALTEZZARIGHELLO);

  ctx.fillStyle = '#7f8388';
  ctx.font = '600 9px ui-monospace,SFMono-Regular,Menlo,monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText('FL', 9, ALTEZZARIGHELLO / 2);

  const pxBattito = d.battito * d.pxSecondo;
  const nBattiti = Math.ceil(durataBrano / d.battito) + 1;
  for (let b = 0; b <= nBattiti; b++){
    const x = Math.round(TASTIERA + b * pxBattito) + .5;
    if (x > d.larghezza) break;
    const battuta = (b % 4 === 0);
    ctx.strokeStyle = battuta ? '#5c6167' : '#3b3f43';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, battuta ? 0 : 12);
    ctx.lineTo(x, ALTEZZARIGHELLO);
    ctx.stroke();
    if (battuta && pxBattito >= 9){
      ctx.fillStyle = '#9a958d';
      ctx.font = '9px ui-monospace,SFMono-Regular,Menlo,monospace';
      ctx.fillText(String(b / 4 + 1), x + 4, ALTEZZARIGHELLO / 2 - 1);
    }
  }

  ctx.fillStyle = '#0b0c0d';
  ctx.fillRect(0, ALTEZZARIGHELLO - 1, d.larghezza, 1);
  ctx.fillRect(TASTIERA - 1, 0, 2, ALTEZZARIGHELLO);
}

// --- sfondo: tastiera, righe dei semitoni, griglia dei tempi, note ---
function costruisciSfondo(){
  const d = rollDisp, s = rollSfondo;
  s.width = Math.round(d.larghezza * d.dpr);
  s.height = Math.round(d.altezza * d.dpr);
  const ctx = s.getContext('2d');
  ctx.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  ctx.fillStyle = '#1f2123';
  ctx.fillRect(0, 0, d.larghezza, d.altezza);

  for (let m = d.max; m >= d.min; m--){
    const y = RIGHEVOLO + (d.max - m) * RIGA;
    const ottava = (m % 12 === 0), nero = tastoNero(m);

    ctx.fillStyle = ottava ? '#2c2f32' : (nero ? '#1d1f21' : '#25282a');
    ctx.fillRect(TASTIERA, y, d.larghezza - TASTIERA, RIGA);
    if (ottava){
      ctx.fillStyle = 'rgba(255,255,255,.055)';
      ctx.fillRect(TASTIERA, y + RIGA - 1, d.larghezza - TASTIERA, 1);
    }

    if (nero){
      ctx.fillStyle = '#141517';
      ctx.fillRect(0, y, TASTIERA, RIGA);
    } else {
      const g = ctx.createLinearGradient(0, y, 0, y + RIGA);
      g.addColorStop(0, '#d8d7d3');
      g.addColorStop(1, '#b4b3ae');
      ctx.fillStyle = g;
      ctx.fillRect(0, y, TASTIERA, RIGA);
    }
    ctx.fillStyle = 'rgba(0,0,0,.42)';
    ctx.fillRect(0, y + RIGA - 1, TASTIERA, 1);

    if (ottava){                             // le etichette dei Do, come in FL
      ctx.fillStyle = '#a95f1d';
      ctx.font = '600 9px ui-monospace,SFMono-Regular,Menlo,monospace';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(nomeNota(m), TASTIERA - 6, y + RIGA / 2 + .5);
    }
  }
  ctx.fillStyle = '#0b0c0d';
  ctx.fillRect(TASTIERA - 1, 0, 2, d.altezza);

  // griglia verticale: suddivisioni, battiti, battute
  const pxBattito = d.battito * d.pxSecondo;
  const pxSuddivisione = pxBattito / 4;
  const nBattiti = Math.ceil(durataBrano / d.battito) + 1;

  if (pxSuddivisione >= 16){
    ctx.strokeStyle = '#2b2f32';
    ctx.lineWidth = .6;
    for (let i = 0; i <= nBattiti * 4; i++){
      if (i % 4 === 0) continue;
      const x = Math.round(TASTIERA + i * pxSuddivisione) + .5;
      if (x > d.larghezza) break;
      ctx.beginPath(); ctx.moveTo(x, RIGHEVOLO); ctx.lineTo(x, d.altezza); ctx.stroke();
    }
  }
  for (let b = 0; b <= nBattiti; b++){
    const x = Math.round(TASTIERA + b * pxBattito) + .5;
    if (x > d.larghezza) break;
    const battuta = (b % 4 === 0);
    ctx.strokeStyle = battuta ? '#52565b' : '#3b3f43';
    ctx.lineWidth = battuta ? 1 : .7;
    ctx.beginPath(); ctx.moveTo(x, RIGHEVOLO); ctx.lineTo(x, d.altezza); ctx.stroke();
  }

  for (const n of evSuonate) disegnaNota(ctx, n, d, false);
}

// --- una nota: rettangolo verde con bordo scuro e luce in alto (stile FL) ---
function disegnaNota(ctx, n, d, accesa){
  const x = TASTIERA + n.start * d.pxSecondo;
  if (x > d.larghezza) return;
  const w = Math.max(3, (n.end - n.start) * d.pxSecondo - 1);
  if (x + w < TASTIERA) return;
  const y = RIGHEVOLO + (d.max - n.note) * RIGA + 1;
  const h = RIGA - 2;
  const v = Math.max(0, Math.min(127, n.velocity || 100)) / 127;
  const l = 29 + v * 15;                     // la velocity schiarisce la nota

  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, 'hsl(' + TONO_NOTA + ' 45% ' + (l + 11) + '%)');
  g.addColorStop(.55, 'hsl(' + TONO_NOTA + ' 46% ' + l + '%)');
  g.addColorStop(1, 'hsl(' + TONO_NOTA + ' 48% ' + (l - 8) + '%)');
  ctx.fillStyle = g;
  percorsoArrotondato(ctx, x, y, w, h, 2);
  ctx.fill();
  ctx.strokeStyle = 'hsl(' + TONO_NOTA + ' 52% ' + Math.max(12, l - 17) + '%)';
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = 'rgba(255,255,255,.18)';
  percorsoArrotondato(ctx, x + 1, y + 1, Math.max(1, w - 2),
                      Math.max(1, Math.min(2, h * .34)), 1);
  ctx.fill();

  if (accesa){
    ctx.fillStyle = 'rgba(236,255,214,.55)';
    percorsoArrotondato(ctx, x, y, w, h, 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

// --- un fotogramma: sfondo, note accese, tasti illuminati, testina ---
function disegnaRoll(pos){
  if (!rollPronto || !rollDisp) return;
  const d = rollDisp;
  const cv = document.getElementById('rollCanvas');
  const ctx = cv.getContext('2d');
  ctx.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  ctx.clearRect(0, 0, d.larghezza, d.altezza);
  ctx.drawImage(rollSfondo, 0, 0, d.larghezza, d.altezza);

  const posizione = Math.max(0, Math.min(durataBrano, pos || 0));

  for (let i = 0; i < evSuonate.length; i++){
    const n = evSuonate[i];
    if (n.start > posizione) break;          // le note sono ordinate per inizio
    if (n.end <= posizione) continue;
    disegnaNota(ctx, n, d, true);
    const y = RIGHEVOLO + (d.max - n.note) * RIGA;
    ctx.fillStyle = 'rgba(126,190,92,.8)';   // il tasto si accende, come in FL
    ctx.fillRect(0, y, TASTIERA - 2, RIGA - 1);
  }

  const x = TASTIERA + posizione * d.pxSecondo;
  if (x >= TASTIERA && x <= d.larghezza){
    ctx.fillStyle = 'rgba(255,255,255,.14)';
    ctx.fillRect(x - .5, 0, 2, d.altezza);
    ctx.fillStyle = 'rgba(255,255,255,.88)';
    ctx.fillRect(x - .5, 0, 1, d.altezza);
  }
  disegnaRighello(posizione, x);

  // quando si è ingranditi la vista segue la testina
  const vista = document.getElementById('rollVista');
  if (vista && inRiproduzione && vista.scrollWidth > vista.clientWidth + 4){
    const sinistra = vista.scrollLeft, destra = sinistra + vista.clientWidth;
    if (x < sinistra + 34 || x > destra - 70)
      vista.scrollLeft = Math.max(0, x - vista.clientWidth * .3);
  }
  sincronizzaRighello();
}

// --- righello: sfondo fisso + triangolino della testina, sincronizzato in orizzontale ---
function disegnaRighello(_posizione, x){
  const d = rollDisp;
  const cv = document.getElementById('righelloCanvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  ctx.setTransform(d.dpr, 0, 0, d.dpr, 0, 0);
  ctx.clearRect(0, 0, d.larghezza, ALTEZZARIGHELLO);
  ctx.drawImage(rollRighello, 0, 0, d.larghezza, ALTEZZARIGHELLO);
  if (x >= TASTIERA && x <= d.larghezza){
    ctx.beginPath();
    ctx.moveTo(x - 4, 1);
    ctx.lineTo(x + 4, 1);
    ctx.lineTo(x, 9);
    ctx.closePath();
    ctx.fillStyle = '#f4f1ec';
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.9)';
    ctx.fillRect(x - .5, 0, 1, ALTEZZARIGHELLO);
  }
}

function sincronizzaRighello(){
  const cv = document.getElementById('righelloCanvas');
  const vista = document.getElementById('rollVista');
  if (!cv || !vista) return;
  cv.style.transform = 'translateX(' + (-vista.scrollLeft) + 'px)';
}

function centraRoll(){
  const vista = document.getElementById('rollVista');
  if (!vista) return;
  vista.scrollLeft = 0;
  vista.scrollTop = Math.max(0, (vista.scrollHeight - vista.clientHeight) / 2);
  sincronizzaRighello();
}

function secondoDalPuntatore(ev){
  if (!rollDisp) return 0;
  const r = document.getElementById('rollCanvas').getBoundingClientRect();
  const x = ev.clientX - r.left;
  return Math.max(0, Math.min(durataBrano, (x - TASTIERA) / rollDisp.pxSecondo));
}

function cambiaZoom(fattore){
  if (!rollPronto || !rollDisp) return;
  const vista = document.getElementById('rollVista');
  const testina = TASTIERA + posCorrente * rollDisp.pxSecondo;
  const scostamento = testina - vista.scrollLeft;
  zoomRoll = Math.max(1, Math.min(24, zoomRoll * fattore));
  preparaRoll();
  if (rollDisp)
    vista.scrollLeft = Math.max(0, TASTIERA + posCorrente * rollDisp.pxSecondo - scostamento);
  disegnaRoll(posCorrente);
}

// --- clic e trascinamento sul piano roll: spostamento nel brano ---
const vistaRoll = document.getElementById('rollVista');
vistaRoll.addEventListener('scroll', () => {
  if (rollPronto) sincronizzaRighello();
}, {passive: true});
vistaRoll.addEventListener('pointerdown', (ev) => {
  if (!rollPronto || ev.button !== 0) return;
  if (ev.target.id !== 'rollCanvas') return;      // il bordo/le barre non spostano
  trascinoRoll = true;
  riprendiDopoTrascino = inRiproduzione;
  if (riprendiDopoTrascino) fermaPlayback();
  posCorrente = secondoDalPuntatore(ev);
  aggiornaBarra(posCorrente);
  disegnaRoll(posCorrente);
  try { vistaRoll.setPointerCapture(ev.pointerId); } catch (e) {}
  ev.preventDefault();
});
vistaRoll.addEventListener('pointermove', (ev) => {
  if (!trascinoRoll) return;
  posCorrente = secondoDalPuntatore(ev);
  aggiornaBarra(posCorrente);
  disegnaRoll(posCorrente);
});
['pointerup','pointercancel'].forEach((nome) => vistaRoll.addEventListener(nome, (ev) => {
  if (!trascinoRoll) return;
  trascinoRoll = false;
  try { vistaRoll.releasePointerCapture(ev.pointerId); } catch (e) {}
  if (riprendiDopoTrascino){ riprendiDopoTrascino = false; avvia(); }
}));

document.getElementById('zoomIn').addEventListener('click', () => cambiaZoom(1.6));
document.getElementById('zoomOut').addEventListener('click', () => cambiaZoom(1 / 1.6));
document.getElementById('zoomFit').addEventListener('click', () => {
  zoomRoll = 1;
  preparaRoll();
  centraRoll();
  disegnaRoll(posCorrente);
});

// anche il righello sposta il punto di ascolto, come in FL Studio
document.getElementById('rollRighello').addEventListener('pointerdown', (ev) => {
  if (!rollPronto || ev.button !== 0) return;
  posCorrente = secondoDalPuntatore(ev);
  aggiornaBarra(posCorrente);
  disegnaRoll(posCorrente);
  if (inRiproduzione) avvia();
  ev.preventDefault();
});

let attesaRoll = null;
window.addEventListener('resize', () => {
  if (!rollPronto) return;
  clearTimeout(attesaRoll);
  attesaRoll = setTimeout(() => { preparaRoll(); disegnaRoll(posCorrente); }, 150);
});

// --- legge un file MIDI (formati 0/1, running status, cambi di tempo) ---
function leggiMidi(buf){
  const dv = new DataView(buf), b = new Uint8Array(buf);
  const u8 = (i) => b[i];
  const u32 = (i) => dv.getUint32(i, false);
  const firma = (i) => String.fromCharCode(b[i], b[i + 1], b[i + 2], b[i + 3]);
  if (buf.byteLength < 14 || firma(0) !== 'MThd') throw new Error('non è un file MIDI');

  const divisione = dv.getUint16(12);
  if (divisione & 0x8000) throw new Error('divisione SMPTE non supportata');
  const tpb = divisione || 480;                       // tick per battuta

  const grezze = [], tempi = [{tick: 0, us: 500000}];
  let p = 8 + u32(4), letti = 0;

  while (p + 8 <= buf.byteLength && letti++ < 500000){
    if (firma(p) !== 'MTrk'){ p += 8 + u32(p + 4); continue; }
    const fine = Math.min(buf.byteLength, p + 8 + u32(p + 4));
    p += 8;
    let tick = 0, status = 0, c;
    const aperte = {};

    while (p < fine){
      let delta = 0;
      do { c = u8(p++); delta = (delta << 7) | (c & 0x7f); } while (c & 0x80);
      tick += delta;
      if (p >= fine) break;

      if (u8(p) & 0x80){ status = u8(p); p++; }
      else if (!status) break;                        // dato senza status: file rotto

      if (status === 0xff){
        const meta = u8(p++);
        let len = 0;
        do { c = u8(p++); len = (len << 7) | (c & 0x7f); } while (c & 0x80);
        if (meta === 0x51 && len === 3)
          tempi.push({tick, us: (u8(p) << 16) | (u8(p + 1) << 8) | u8(p + 2)});
        p += len;
        status = 0;                                   // il meta non lascia running status
      } else if (status === 0xf0 || status === 0xf7){
        let len = 0;
        do { c = u8(p++); len = (len << 7) | (c & 0x7f); } while (c & 0x80);
        p += len;
        status = 0;
      } else {
        const tipo = status & 0xf0, canale = status & 0x0f;
        if (tipo === 0x90 || tipo === 0x80){
          const nota = u8(p), vel = u8(p + 1); p += 2;
          const k = canale + ':' + nota;
          if (tipo === 0x90 && vel > 0) aperte[k] = {tick, vel};
          else if (aperte[k]){
            grezze.push({on: aperte[k].tick, off: tick, nota, vel: aperte[k].vel});
            delete aperte[k];
          }
        } else if (tipo === 0xc0 || tipo === 0xd0) p += 1;
        else p += 2;
      }
      if (p > fine) break;
    }
    for (const k in aperte)                           // note lasciate aperte a fine traccia
      grezze.push({on: aperte[k].tick, off: tick, nota: parseInt(k.split(':')[1], 10),
                   vel: aperte[k].vel});
    p = fine;
  }

  // tick -> secondi, rispettando i cambi di tempo
  tempi.sort((a, b) => a.tick - b.tick);
  const seg = [];
  for (const t of tempi){
    // a pari tick vince l'evento vero: il primo elemento e' il default 120 BPM
    if (seg.length && t.tick === seg[seg.length - 1].tick) seg[seg.length - 1] = t;
    else seg.push(t);
  }
  if (!seg.length || seg[0].tick > 0) seg.unshift({tick: 0, us: 500000});
  const secAt = (t) => {
    let s = 0;
    for (let i = 0; i < seg.length; i++){
      const t0 = seg[i].tick, t1 = (i + 1 < seg.length) ? seg[i + 1].tick : Infinity;
      s += Math.max(0, Math.min(t, t1) - t0) / tpb * (seg[i].us / 1e6);
      if (t <= t1) break;
    }
    return s;
  };

  const note = grezze
    .filter((n) => n.off > n.on && n.nota >= 0 && n.nota <= 127)
    .map((n) => ({note: n.nota, velocity: n.vel, start: secAt(n.on), end: secAt(n.off)}))
    .sort((a, b) => a.start - b.start || a.note - b.note);
  for (const n of note) n.duration = Math.max(0.02, n.end - n.start);
  return note;
}

// --- audio ---
function contesto(){
  if (!audioCtx){
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    master = audioCtx.createGain();
    master.gain.value = 0.55;
    const comp = audioCtx.createDynamicsCompressor();  // niente "sfondamenti" sugli accordi
    comp.threshold.value = -12; comp.ratio.value = 4;
    master.connect(comp); comp.connect(audioCtx.destination);
  }
  return audioCtx;
}

function suonaNota(midi, velocity, durata, quando){
  const c = contesto();
  const freq = 440 * Math.pow(2, (midi - 69) / 12);
  const vol = Math.max(0.02, (velocity / 127) * 0.28);
  const osc = c.createOscillator(), osc2 = c.createOscillator();
  const g = c.createGain(), f = c.createBiquadFilter();
  osc.type = 'triangle'; osc.frequency.value = freq;
  osc2.type = 'sine';    osc2.frequency.value = freq * 2;
  f.type = 'lowpass'; f.frequency.value = 3200;
  osc.connect(f); osc2.connect(f); f.connect(g); g.connect(master);
  const att = 0.008, ril = Math.min(0.4, Math.max(0.06, durata * 0.5));
  const t0 = Math.max(quando, c.currentTime + 0.01);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.linearRampToValueAtTime(vol, t0 + att);
  g.gain.setValueAtTime(vol, t0 + Math.max(att, durata));
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + durata + ril);
  osc.start(t0); osc2.start(t0);
  const stop = t0 + durata + ril + 0.05;
  osc.stop(stop); osc2.stop(stop);

  const gruppo = [osc, osc2];
  osc2.onended = () => {            // libera i nodi: sessioni lunghe senza perdite di memoria
    attivi.delete(gruppo);
    [osc, osc2, f, g].forEach((n) => { try { n.disconnect(); } catch (e) {} });
  };
  return gruppo;
}

function fermaSuoni(){
  for (const gruppo of attivi)
    for (const o of gruppo) { try { o.stop(); } catch (e) {} }
  attivi.clear();
}

function riempiFinestra(finoA){
  while (prossima < evSuonate.length && evSuonate[prossima].start <= finoA){
    const n = evSuonate[prossima++];
    attivi.add(suonaNota(n.note, n.velocity, n.duration, tOrigine + n.start));
  }
}

function tick(){
  if (!inRiproduzione) return;
  const c = contesto();
  const pos = Math.max(0, c.currentTime - tOrigine);   // NON clampata: serve a capire che è finito
  riempiFinestra(pos + FINESTRA);
  aggiornaBarra(pos);
  disegnaRoll(pos);
  if (pos >= durataBrano + 0.12){
    if (loopChk.checked){ posCorrente = 0; avvia(); return; }
    inRiproduzione = false; fermaSuoni(); raf = null;
    playBtn.textContent = '▶ Ascolta';
    posCorrente = 0; aggiornaBarra(0); disegnaRoll(0);
    return;
  }
  raf = requestAnimationFrame(tick);
}

function avvia(){
  if (!evSuonate.length) return;
  const c = contesto();
  if (c.state === 'suspended') c.resume();
  if (posCorrente >= durataBrano - 0.05) posCorrente = 0;

  fermaSuoni();
  inRiproduzione = true;
  playBtn.textContent = '⏸ Pausa';
  const base = c.currentTime + 0.08;
  tOrigine = base - posCorrente;

  // note già iniziate prima del punto di partenza ma ancora in suono
  for (let i = 0; i < evSuonate.length; i++){
    const n = evSuonate[i];
    if (n.start >= posCorrente) break;
    if (n.end > posCorrente)
      attivi.add(suonaNota(n.note, n.velocity, Math.min(n.duration, n.end - posCorrente), base));
  }

  prossima = 0;
  while (prossima < evSuonate.length && evSuonate[prossima].start < posCorrente) prossima++;
  riempiFinestra(posCorrente + FINESTRA);

  if (raf) cancelAnimationFrame(raf);
  raf = requestAnimationFrame(tick);
}

function fermaPlayback(){
  if (inRiproduzione && audioCtx)
    posCorrente = Math.min(durataBrano, Math.max(0, audioCtx.currentTime - tOrigine));
  inRiproduzione = false;
  fermaSuoni();
  if (raf){ cancelAnimationFrame(raf); raf = null; }
  playBtn.textContent = '▶ Ascolta';
  aggiornaBarra(posCorrente);
  disegnaRoll(posCorrente);
}

// --- caricamento del MIDI appena prodotto ---
let versioneCaricamento = 0;

async function caricaMidi(id){
  const mia = ++versioneCaricamento;     // se parte un'altra conversione, questo caricamento è vecchio
  const scatolaRoll = document.getElementById('roll');
  fermaPlayback();
  evSuonate = []; durataBrano = 0; posCorrente = 0;
  rollPronto = false; zoomRoll = 1;      // nuovo brano: il piano roll riparte adattato
  if (scatolaRoll) scatolaRoll.style.display = 'none';
  playBtn.disabled = true;
  playBtn.textContent = '▶ Ascolta';
  avviso.style.display = 'none';
  aggiornaBarra(0);
  try {
    const r = await fetch('/api/midi/' + id + '?ascolta=1');
    if (!r.ok) throw new Error('il server ha risposto ' + r.status);
    const lette = leggiMidi(await r.arrayBuffer());
    if (mia !== versioneCaricamento) return;
    evSuonate = lette;
    durataBrano = evSuonate.reduce((m, n) => Math.max(m, n.end), 0);
    aggiornaBarra(0);
    if (!evSuonate.length){
      avviso.textContent = 'Il MIDI non contiene note da riprodurre.';
      avviso.style.display = 'block';
      return;
    }
    if (scatolaRoll) scatolaRoll.style.display = '';   // visibile: si può misurarne la larghezza
    preparaRoll();
    centraRoll();
    disegnaRoll(0);
    playBtn.disabled = false;
  } catch (e) {
    if (mia !== versioneCaricamento) return;
    avviso.textContent = 'Player non disponibile (' + e.message + '). Il download funziona comunque.';
    avviso.style.display = 'block';
  }
}

playBtn.addEventListener('click', () => { inRiproduzione ? fermaPlayback() : avvia(); });

barra.addEventListener('click', (ev) => {          // clic sulla barra = spostati nel brano
  if (!durataBrano) return;
  const r = barra.getBoundingClientRect();
  posCorrente = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * durataBrano;
  if (inRiproduzione) avvia();
  else { aggiornaBarra(posCorrente); disegnaRoll(posCorrente); }
});
</script>

</body>
</html>
"""


# -----------------------------------------------------------------------------
#  avvio
# -----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Sito locale: video di FL Studio -> MIDI")
    ap.add_argument("--porta", type=int, default=PORTA,
                    help="porta di ascolto (predefinita: %d, oppure la variabile PORTA)" % PORTA)
    ap.add_argument("--apri", action="store_true", help="apre il browser all'avvio")
    a = ap.parse_args()

    if not PROGRAMMA.is_file():
        print("ERRORE: non trovo %s" % PROGRAMMA)
        return 2

    url = "http://127.0.0.1:%d" % a.porta
    print("Sito video -> midi su %s   (Ctrl+C per fermarlo)" % url)
    if a.apri:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=a.porta, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
