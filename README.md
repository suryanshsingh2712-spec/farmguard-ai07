# FarmGuard AI — Ready-to-run MVP

This implementation follows the supplied SIH presentation: farmer account, farm profile, weather intelligence, crop-image early risk signal, disease/risk engine, irrigation recommendation, market-ready architecture, 7-day action plan, explainable recommendations, and a generated farm situation video.

## Run locally

```bash
cd farmguard-ai
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Windows PowerShell: $env:FARMGUARD_SECRET="your-long-secret"
# macOS/Linux: export FARMGUARD_SECRET="your-long-secret"
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000

## What is live
- Real email/password registration and login with hashed passwords + expiring JWT sessions.
- SQLite persistence for users, farms, activities and analyses.
- Real 7-day weather retrieval through Open-Meteo when available, with a transparent fallback for demos.
- Crop image upload and an early-risk scoring engine using farm/weather context. It is intentionally labelled as decision support, not a definitive diagnosis.
- 7-day farmer action plan.
- Server-generated MP4 situation report from the current farm analysis.
- Responsive farmer-friendly UI.

## Production upgrades
1. Replace SQLite with PostgreSQL.
2. Put the API behind HTTPS and a production reverse proxy.
3. Replace the baseline crop-risk heuristic with a validated PyTorch model trained/evaluated on permitted agricultural data.
4. Connect an official/authorized market-price feed before presenting live mandi prices.
5. Connect an AI video provider if you want photorealistic generative scenes/avatar narration; the included video generator is a real generated report video, not a claim of photorealistic generative AI.
6. Add email-based password reset and rate limiting before public launch.

The authentication approach uses FastAPI's documented OAuth2/JWT pattern; weather uses the documented Open-Meteo forecast API shape.
