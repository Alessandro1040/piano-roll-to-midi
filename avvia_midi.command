#!/bin/zsh
# ---------------------------------------------------------------
#  Avvia il sito "Video -> MIDI" su http://127.0.0.1:7864
#  Doppio clic su questo file (macOS), oppure da terminale:
#      ./avvia_midi.command
#  Se hai creato il virtualenv ".venv" nella cartella, lo usa da solo.
# ---------------------------------------------------------------
cd "$(dirname "$0")" || exit 1

PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

if lsof -nP -iTCP:7864 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Il sito e' gia' in ascolto su http://127.0.0.1:7864"
  open http://127.0.0.1:7864
  exit 0
fi

echo "Avvio Video -> MIDI su http://127.0.0.1:7864 ..."
(sleep 2 && open http://127.0.0.1:7864) &
exec "$PY" web_video2midi.py --porta 7864
