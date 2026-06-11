# Paytm Vendor Risk Assessment (VRA) Tool

Internal web application for Paytm’s compliance team: capture vendor identifiers, run LLM-backed OSINT (Google Gemini with Search grounding), validate sources, and export a structured **PDF** report with an **audit trail** and optional **batch Excel** processing.

The frontend is a **React 19 (Vite 8) SPA** in `frontend/` styled with **Tailwind CSS v4** (compiled via PostCSS — no CDN); the backend is a **FastAPI** JSON API in `app/` on **Python 3.13**. In production both are deployed to **Netlify** — the React app is served as static files and the Python API runs as a Netlify Function via `mangum`.

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
                            ┌───────────▼─────────────────────────┐
                            │ Collectors (hybrid) → EvidencePack   │
                            │  GST · MCA · News RSS · Web search   │
                            └───────────┬─────────────────────────┘
                                        │
                            ┌───────────▼───────────┐
                            │ risk/ framework       │
                            │  source credibility,  │
                            │  fact class, adverse  │
                            │  media, litigation,   │
                            │  confidence, rec.     │
                            └───────────┬───────────┘
                                        │
                            ┌───────────▼───────────────────────┐
                            │ LLMProvider                        │
                            │  Gemini · OpenAI · Anthropic ·     │
                            │  OpenRouter                        │
                            └───────────┬───────────────────────┘
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
python3.13 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

> If you move or rename the project folder, recreate the venv
> (`python3.13 -m venv .venv --clear && pip install -r requirements.txt`) —
> the console-script shebangs are pinned to the original path.

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

### Risk-intelligence framework (`app/core/risk/`)

Hybrid synthesis runs raw OSINT evidence through an **entity-agnostic** scoring
framework before any LLM prose is generated. None of these modules contain
vendor-specific logic — they key only off the *shape* and *provenance* of
evidence, so a listed company, an LLP, an NGO, and a foreign entity are scored
the same way:

- **`source_credibility`** — Tier 1–4 trust model for every citation.
- **`fact_classification`** — VERIFIED FACT / MEDIA REFERENCE / INFERENCE.
- **`litigation`** — classifies litigation by *nature* (fraud vs routine commercial dispute) and outcome, not "exists ⇒ risk".
- **`adverse_media`** — multi-dimensional article relevance/impact scoring with recency dampening.
- **`entity_resolution`** — resolves an input string to one legal entity.
- **`confidence`** — evidence-quality confidence, independent of the risk band.
- **`recommendation`** — six-tier, evidence-gated recommendation engine.

This is what stops the two failure modes the recent commits target: spurious
HIGH flags from vendor-as-protector news, and all-LOW reports for vendors with
genuine adverse media.

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
│   │   ├── collectors/        # GST, MCA, news RSS, web search → EvidencePack
│   │   ├── llm/               # gemini, openai, anthropic, openrouter, factory
│   │   ├── risk/              # entity-agnostic risk-intelligence framework
│   │   ├── hybrid_report.py   # collectors + deterministic synthesis
│   │   ├── narrative.py       # prose generation for report sections
│   │   └── timeutil.py        # naive-UTC helper (replaces datetime.utcnow)
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
- **Keep exactly one `FERNET_KEY` in `.env`.** If it changes (or a duplicate line shadows it), previously stored keys can no longer be decrypted — Settings shows `(decrypt error)`. Recovery: restore the original `FERNET_KEY`, or delete the unreadable key in Settings and re-add it.
- `xlsx` (SheetJS) on the npm registry carries a known prototype-pollution / ReDoS advisory with no registry fix. For the patched build, install from the official CDN: `npm install https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz`.
