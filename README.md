# EzeAgent

A hybrid AI agent starter platform with a FastAPI backend, responsive React dashboard, OpenAI-compatible cloud/local provider routing, workspace inspection, reviewable patches, approval-gated patch application, and SQLite activity logs.

## Implemented
- FastAPI health, provider listing/testing, model discovery, chat, workspace list/read, patch proposal, approval/rejection, and activity APIs.
- OpenRouter and OpenAI hosted adapters through their OpenAI-compatible chat/model endpoints.
- Optional configurable OpenAI-compatible endpoint (LM Studio, Ollama with compatible API, vLLM, llama.cpp server).
- Workspace boundary checks, size limits, hash-bound patch proposals, expiration, stale-file checks, and audit trail.
- React/Vite dashboard for chat, provider status, workspace, approvals, and activity.

## Limitations
- Anthropic and Gemini native APIs are not implemented. Do not interpret OpenAI-compatible routing as native support for every vendor.
- Streaming, tool/function calling, autonomous agent loops, authentication, restricted test execution, Tauri desktop wrapper, and browser/GUI automation are not implemented. The local dashboard can open Word or Excel, open Command Prompt, and run a command after you review and confirm it.
- Approval endpoints assume a trusted local user. Do not expose the backend to untrusted networks without authentication, TLS, and additional hardening.
- The API currently uses a configured workspace root; set `EZEAGENT_WORKSPACE` to the project you intend to inspect.

## Requirements
Python 3.11+, Node.js 20+.

## Start backend
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn backend.main:app --reload
```
API docs: http://127.0.0.1:8000/docs

## Start frontend
```bash
cd apps/web
npm install
npm run dev
```
The dashboard expects the backend at `http://127.0.0.1:8000`; set `VITE_API_BASE_URL` before starting Vite to change it.

## Configure AI
Set `OPENROUTER_API_KEY` or `OPENAI_API_KEY` in backend `.env`. Keys are read only by the backend. For a compatible endpoint, configure `COMPATIBLE_API_BASE_URL`, `COMPATIBLE_API_KEY`, and `COMPATIBLE_MODEL`. Never commit `.env`.

## Tests
```bash
pytest
cd apps/web && npm install && npm run build
```
Run these commands in your environment; no test or build result is claimed by this repository change alone.

## Security
No arbitrary shell execution or automatic GitHub writes. Treat workspace files and model responses as untrusted. Use a dedicated, non-sensitive workspace when experimenting. Add authentication and deployment-specific hardening before network exposure.

## Desktop and license
The web app works independently. Tauri desktop packaging is planned but not implemented or verified. No license existed in the initial repository; choose and add one before distributing the project.


