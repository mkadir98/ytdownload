#!/bin/bash
cd "$(dirname "$0")"

# Python 3.10+ bul (macOS ile gelen /usr/bin/python3 genelde 3.9'dur ve yetmez)
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        PY="$c"; break
    fi
done

if [ -z "$PY" ]; then
    echo ""
    echo "HATA: Python 3.10 veya daha yeni bir sürüm bulunamadı."
    echo "Bu Mac'teki python3 sürümü: $(python3 --version 2>&1)"
    echo ""
    echo "Lütfen https://www.python.org/downloads/ adresinden Python'u kurun,"
    echo "sonra bu komutu tekrar çalıştırın."
    open "https://www.python.org/downloads/" 2>/dev/null
    exit 1
fi
echo "Kullanılan Python: $("$PY" --version)"

# Eski (uyumsuz) sanal ortam varsa sil
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    rm -rf .venv
fi
if [ ! -d .venv ]; then
    echo "İlk kurulum yapılıyor, lütfen bekleyin..."
    "$PY" -m venv .venv || exit 1
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q --upgrade -r requirements.txt || exit 1
python main.py
