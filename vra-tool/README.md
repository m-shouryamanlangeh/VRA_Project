# Paytm Vendor Risk Assessment (VRA) Tool

Internal web application for Paytm’s compliance team: capture vendor identifiers, run LLM-backed OSINT (Google Gemini with Search grounding), validate sources, and export a structured **PDF** report with an **audit trail** and optional **batch Excel** processing.

## Architecture (ASCII)

```
┌─────────────┐     ┌──────────────────────────────────────┐
│   Browser   │────▶│  FastAPI (app/main.py)               │
│  Tailwind   │     │  /generate  /generate/batch          │
│  + JS       │     │  /settings  /audit  /download/pdf    │
└─────────────┘     └───────────┬──────────────────────────┘
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
                    │ WeasyPrint ← pdf tpl  │
                    └───────────────────────┘
```

## Setup

```bash
git clone <repo-url>
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

Run the app:

```bash
./run.sh
# or
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://localhost:8000](http://localhost:8000). On first use go to **Settings**, add a **Primary** Gemini API key (stored **encrypted**), then **Test Connection** and **Save**.

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
3. **Wire** Settings + key storage: extend `ApiKey.provider` values, add UI option in `templates/settings.html`, and branch in `app/core/vra_service.py` if the orchestration differs from Gemini.

## Sample test vendor

- **Name:** SHARP PENCIL PRODUCTIONS  
- **GST:** `27ADKFS8129B1ZY`  
- **Org type:** Partnership  

## Project layout

See the repository tree: `app/` (FastAPI, core, prompts), `templates/`, `static/`, `output/` (generated PDFs), `data/vra.db` (SQLite, local dev).

### Database migrations

The app uses `create_all` on startup. If you change ORM columns (e.g. added `request_type` / `error_message`), delete `data/vra.db` locally and restart to recreate tables.

## Security notes

- API keys are **never** stored in plaintext in the database; they are encrypted with **Fernet** (`FERNET_KEY`).
- PDFs only include findings that pass URL validation (see `app/core/validator.py`).
- Prompt files under `app/prompts/` are stakeholder-owned — do not edit in forks without approval.
