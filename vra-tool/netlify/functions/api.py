"""Netlify / AWS Lambda entry point — wraps the FastAPI app via mangum.

Netlify rewrites e.g. ``/generate`` to ``/.netlify/functions/api/generate``.
``api_gateway_base_path`` tells Mangum to strip that prefix so FastAPI sees
the original path (``/generate``).
"""

from mangum import Mangum

from app.main import app

handler = Mangum(
    app,
    lifespan="off",
    api_gateway_base_path="/.netlify/functions/api",
)
