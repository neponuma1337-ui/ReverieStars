#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y
pkg install python unzip -y
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
mkdir -p data
printf '\nУстановка завершена. Отредактируй .env, затем запусти: python app.py\n'
