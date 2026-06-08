# Paytm Vendor Risk Assessment (VRA) Tool

Internal web application for Paytm’s compliance team: capture vendor identifiers, run LLM-backed OSINT (Google Gemini with Search grounding), validate sources, and export a structured **PDF** report with an **audit trail** and optional **batch Excel** processing.

The frontend is a **React (Vite) SPA** in `frontend/`; the backend is a **FastAPI** JSON API in `app/`. In production both are deployed to **Netlify** — the React app is served as static files and the Python API runs as a Netlify Function via `mangum`.

## Architecture

```
┌─────────────────────┐     ┌──────────────────────────────────────┐
│  React SPA (Vite)   │────▶│  FastAPI API (app/main.py)           │
│  frontend/src/*     │     │  /generate  /generate/batch          │
│  Tailwind via CDN   │     │  /api/settings  /api/audit           │
└─────────────────────┘     │  /download/pdf/{filename}            │
                            └───────────┬──────────────────────────┘
                                        │
                            ┌───────────▼───────────┐
                            │ SQLite (SQLAlchemy)   │
                            │ settings, api_keys,   │
                            │ audit_logs, quota     │
                            └───────────┬───────────┘
                                        │
                            ┌───────────▼───────────┐
                            │ LLMProvider           │
                            │  └─ Gemini (search)   │
                            │  └─ OpenAI/Claude stub│
                            └───────────┬───────────┘
                                        │
                            ┌───────────▼───────────┐
                            │  ReportLab → PDF      │
                            └───────────────────────┘
```

## Local development

You run the **backend** (FastAPI) and the **frontend** (Vite) as two processes. Vite proxies API calls to FastAPI in dev — see `frontend/vite.config.js`.

### 1. Backend

```bash
cd vra-tool
python3.11 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Generate a Fernet key and add it to `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Fill `FERNET_KEY=` in `.env`. Optionally set `GEMINI_API_KEY=` for a bootstrap key before using the Settings UI.

Start the API on port 8000:

```bash
./run.sh
# or
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. Frontend

In a second terminal:

```bash
cd vra-tool/frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). On first use go to **Settings**, add a **Primary** Gemini API key (stored **encrypted**), then **Test Connection** and **Save**.

## Deploying to Netlify

The repo is already configured for Netlify:

- `netlify.toml` — builds React (`cd frontend && npm install && npm run build`), publishes `frontend/dist`, and rewrites API paths to the function.
- `netlify/functions/api.py` — wraps the FastAPI app with `mangum` for Lambda.
- `requirements.txt` — Python dependencies installed by Netlify into the function bundle.

In the Netlify dashboard, set these **environment variables** before deploying:

| Variable | Value |
|---|---|
| `FERNET_KEY` | output of the Fernet command above |
| `GEMINI_API_KEY` | optional bootstrap Gemini key |
| `LOG_LEVEL` | `INFO` |

Caveats: Netlify Functions run on Lambda where only `/tmp` is writable, so the SQLite DB and generated PDFs live there and are **ephemeral** across cold starts. For persistent audit history move `DATABASE_URL` to a hosted Postgres later.

### Hybrid mode (collectors + synthesis)

Set `USE_HYBRID_MODE=true` in `.env` to run Python collectors first (**live** GST API + Google News RSS per request), then a **single** Gemini call for synthesis with **`use_search=False`** (no Google Search grounding). There is **no local blacklist file cache** in this pipeline—each run fetches fresh collector outputs. The legacy two-pass + search path remains available when the flag is unset.

### Gemini API key (free tier)

Create a key in Google AI Studio: [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)

## Tests

```bash
pytest tests/ -v
```

The Sharp Pencil end-to-end test calls the real API and is marked `@pytest.mark.slow`:

```bash
export GEMINI_API_KEY="your-key"
export FERNET_KEY="your-fernet-secret"
pytest tests/test_sharp_pencil.py -v
```

## Adding a new LLM provider (3 steps)

1. **Implement** `LLMProvider` in `app/core/llm/<provider>.py` (`generate`, `test_connection`) using the same structured JSON contract as Gemini where possible.
2. **Register** the name in `app/core/llm/factory.py` (`get_provider`).
3. **Wire** Settings + key storage: extend `ApiKey.provider` values, add UI option in `frontend/src/pages/SettingsPage.jsx` (the `MODEL_OPTIONS` / provider `<select>`), and branch in `app/core/vra_service.py` if the orchestration differs from Gemini.

## Sample test vendor

- **Name:** SHARP PENCIL PRODUCTIONS  
- **GST:** `27ADKFS8129B1ZY`  
- **Org type:** Partnership  

## Project layout

```
vra-tool/
├── app/                       # FastAPI backend (JSON API only)
│   ├── core/                  # LLM, collectors, PDF, crypto, validation
│   ├── prompts/               # Stakeholder-owned prompt files
│   ├── routes/                # vendor.py, settings.py, audit.py
│   ├── main.py
│   └── ...
├── frontend/                  # React + Vite SPA
│   ├── src/
│   │   ├── pages/             # HomePage, ResultPage, AuditPage, SettingsPage
│   │   ├── Layout.jsx
│   │   ├── ToastContext.jsx
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── index.html
│   └── vite.config.js
├── netlify/functions/api.py   # Mangum Lambda wrapper
├── netlify.toml
├── requirements.txt
├── data/                      # blacklists/, vra.db (local dev)
├── output/                    # generated PDFs (local dev)
└── tests/                     # pytest suite
```

### Database migrations

The app uses `create_all` on startup. If you change ORM columns (e.g. added `request_type` / `error_message`), delete `data/vra.db` locally and restart to recreate tables.

## Security notes

- API keys are **never** stored in plaintext in the database; they are encrypted with **Fernet** (`FERNET_KEY`).
- PDFs only include findings that pass URL validation (see `app/core/validator.py`).
- Prompt files under `app/prompts/` are stakeholder-owned — do not edit in forks without approval.
