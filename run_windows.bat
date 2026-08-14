@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
set FARMGUARD_SECRET=change-this-before-production
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
