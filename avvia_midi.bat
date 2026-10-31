@echo off
REM ---------------------------------------------------------------
REM  Avvia il sito "Video -> MIDI" su http://127.0.0.1:7864
REM  Doppio clic su questo file (Windows).
REM  Se esiste il virtualenv ".venv" nella cartella, lo usa da solo.
REM ---------------------------------------------------------------
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo Avvio Video -^> MIDI su http://127.0.0.1:7864 ...
start "" http://127.0.0.1:7864
%PY% web_video2midi.py --porta 7864
pause
