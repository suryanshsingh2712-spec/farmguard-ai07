#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export FARMGUARD_SECRET="change-this-before-production"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
