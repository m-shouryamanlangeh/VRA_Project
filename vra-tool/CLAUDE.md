# CLAUDE.md — VRA Tool

Guidance for AI agents working in this repo. Keep it current when commands or architecture change.

## What this is

Paytm **Vendor Risk Assessment** tool: a FastAPI JSON API (`app/`) plus a React 19 / Vite 8 SPA (`frontend/`). Operators submit a vendor (name, GST, org type); the backend runs OSINT collectors + an LLM synthesis pass, scores risk, and renders a PDF report with an audit trail. Deploys to Netlify (static SPA + FastAPI-as-Lambda via `mangum`).

## Environment

- **Python 3.13**, venv at `vra-tool/.venv` (built on the python.org framework interpreter). Console-script shebangs are path-pinned — if the folder is renamed, rebuild: `python3.13 -m venv .venv --clear && .venv/bin/python -m pip install -r requirements.txt`.
- **Node 24**, frontend deps under `frontend/node_modules`.
- Secrets live in `vra-tool/.env` (gitignored): `FERNET_KEY` (encrypts stored API keys — keep exactly one line), optional bootstrap `GEMINI_API_KEY`, `USE_HYBRID_MODE`.

## Commands (run from `vra-tool/`)

```bash
# Backend — invoke through the venv's python; the bare `uvicorn` script needs the venv on PATH
PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --reload --port 8000
./run.sh                                   # same thing, if venv is activated / on PATH

# Tests (the 4 @pytest.mark.slow ones hit real APIs / generate PDFs and are skipped by default)
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies API to :8000
npm run build                               # production build into frontend/dist
```

Health check: `curl localhost:8000/health`. API routes: `/generate`, `/generate/batch`, `/api/settings`, `/api/audit`, `/download/pdf/{file}`.

## Architecture map

- `app/main.py` — FastAPI app, logging, `init_db()` (SQLAlchemy `create_all` on startup; delete `data/vra.db` to recreate after ORM column changes).
- `app/core/vra_service.py` — orchestration entry (`generate_vra_bundle`). Branches on `USE_HYBRID_MODE`.
- `app/core/collectors/` — hybrid-mode evidence gathering (GST, MCA, Google News RSS, DuckDuckGo) → `EvidencePack`.
- `app/core/hybrid_report.py` + `narrative.py` — deterministic synthesis + prose from evidence.
- `app/core/risk/` — **entity-agnostic** risk framework (source credibility, fact classification, litigation nature, adverse-media scoring, entity resolution, confidence, recommendation). No vendor-specific logic — operates on evidence shape/provenance only.
- `app/core/llm/` — provider abstraction (`gemini`, `openai`, `anthropic`, `openrouter`) + `factory.get_provider`.
- `app/core/pdf_generator.py` — ReportLab PDF. `app/core/validator.py` — only URL-validated findings reach the PDF.
- `app/core/timeutil.py` — `utcnow()` naive-UTC helper; use it instead of the deprecated `datetime.utcnow()`.
- `app/models.py` / `app/routes/` — ORM (settings, api_keys, audit_logs, quota) and route handlers.

## Two pipelines

- **Hybrid** (`USE_HYBRID_MODE=true`, the prod default): Python collectors fetch live evidence, then **one** Gemini call synthesizes with search grounding **off**. Falls back to the legacy prompt if collectors return zero snippets.
- **Legacy** (flag unset): two-pass Gemini with Google Search grounding.

## Conventions

- Default to the latest Claude models when touching LLM code (see the `claude-api` skill / `app/core/llm/anthropic.py`).
- Prompt files in `app/prompts/` are stakeholder-owned — don't edit without approval.
- New time values: `from app.core.timeutil import utcnow`.
- Tailwind v4 is CSS-first: theme lives in `frontend/src/styles.css` under `@theme` (no `tailwind.config.js`).
