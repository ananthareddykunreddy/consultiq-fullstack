#!/usr/bin/env bash
set -euo pipefail

cd "${DEPLOY_PATH:-/opt/consultiq}"

git fetch origin

git reset --hard origin/master

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart consultiq
sudo systemctl --no-pager --full status consultiq | sed -n '1,30p'

curl -fsS http://127.0.0.1:8011/health
