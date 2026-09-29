@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Ilk kurulum yapiliyor, lutfen bekleyin...
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q --upgrade -r requirements.txt || (echo. & echo HATA: Python 3.10 veya daha yeni gerekli: https://www.python.org/downloads/ & pause & exit /b 1)
start "" pythonw main.py
