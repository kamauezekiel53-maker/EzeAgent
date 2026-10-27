# Development

Backend: Python 3.11+, FastAPI, Pydantic request models, httpx, SQLite. Frontend: React 18, TypeScript, Vite.

Start API: `pip install -e '.[dev]' && uvicorn backend.main:app --reload`.
Start UI: `cd apps/web && npm install && npm run dev`.
Run tests: `pytest`. Build UI: `cd apps/web && npm run build`.

The UI expects `http://127.0.0.1:8000` by default; set `VITE_API_BASE_URL` to override. Do not use a public deployment until authentication, TLS, secret management, CSRF/network controls as appropriate, and rate limits are implemented. Desktop packaging with Tauri is planned and has not been built or tested.
