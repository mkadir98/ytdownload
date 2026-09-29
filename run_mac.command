#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "İlk kurulum yapılıyor, lütfen bekleyin..."
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q --upgrade -r requirements.txt
python main.py
