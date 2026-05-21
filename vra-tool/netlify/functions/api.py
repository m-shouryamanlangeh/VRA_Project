"""Netlify / AWS Lambda entry point — wraps the FastAPI app via mangum.

Netlify rewrites e.g. ``/generate`` to ``/.netlify/functions/api/generate``.
``api_gateway_base_path`` tells Mangum to strip that prefix so FastAPI sees
the original path (``/generate``).
"""

import sys
from pathlib import Path

# included_files in netlify.toml ship `vra-tool/app/**` into the bundle.
# Add the bundle root + vra-tool/ to sys.path so `from app.main` resolves
# regardless of how Netlify lays out the Lambda working directory.
_BUNDLE_ROOT = Path(__file__).resolve().parents[3]
for _candidate in (_BUNDLE_ROOT, _BUNDLE_ROOT / "vra-tool"):
    _p = str(_candidate)
    if _candidate.exists() and _p not in sys.path:
        sys.path.insert(0, _p)

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402

handler = Mangum(
    app,
    lifespan="off",
    api_gateway_base_path="/.netlify/functions/api",
)
