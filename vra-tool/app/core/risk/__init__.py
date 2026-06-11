"""Production-grade vendor due-diligence framework.

This package isolates the *generalized*, entity-agnostic risk-intelligence
building blocks that turn raw OSINT evidence into a defensible assessment:

  • ``source_credibility``  — Tier 1–4 trust model for every citation.
  • ``fact_classification`` — VERIFIED FACT / MEDIA REFERENCE / INFERENCE.
  • ``litigation``          — litigation-nature classification (not "exists ⇒ risk").
  • ``adverse_media``       — multi-dimensional article relevance/impact scoring.
  • ``entity_resolution``   — resolve an input string to one legal entity.
  • ``confidence``          — evidence-quality confidence, independent of risk.
  • ``recommendation``      — six-tier, evidence-gated recommendation engine.

None of these modules contains entity-specific logic. They operate purely on
the shape and provenance of the evidence, so the framework behaves identically
for a listed company, an LLP, an NGO, a startup, or a foreign entity.
"""

from __future__ import annotations
