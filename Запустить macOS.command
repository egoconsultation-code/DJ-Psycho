#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
trap 'echo "Ошибка запуска. Проверьте установку Python 3.12 и сообщение выше."; read -r -p "Нажмите Enter, чтобы закрыть окно..."' ERR
if [ ! -x .venv/bin/python ]; then
    python3.12 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.lock.txt
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
fi
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false --browser.gatherUsageStats false
