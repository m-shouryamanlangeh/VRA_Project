"""Source Credibility Framework — assign a trust tier to every citation.

Compliance teams must be able to see *why* a piece of evidence was allowed to
move a risk score. This module maps any source URL (or named source) to one of
four credibility tiers and a normalized trust weight in [0, 1]:

    Tier 1  (1.00) — Government, courts, regulators, official filings.
    Tier 2  (0.75) — Established news wires / national financial press.
    Tier 3  (0.40) — Industry blogs, company databases, commentary.
    Tier 4  (0.15) — Forums, user-generated content, unknown domains.

Design rules (generalized, no entity-specific logic):
  • A higher-tier source must always be able to OUTWEIGH a lower-tier one.
  • A lower-tier source must NEVER, on its own, drive a veto-class conclusion.
  • Unknown / unparyseable domains default to Tier 4 (least trusted), never
    silently treated as credible.

The tier mapping is matched on the *registrable host* so subdomains
(``press.rbi.org.in``) inherit their parent's tier.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from enum import IntEnum


class SourceTier(IntEnum):
    """Lower number = more trustworthy."""

    GOVERNMENT = 1
    ESTABLISHED_MEDIA = 2
    INDUSTRY_COMMENTARY = 3
    UNVERIFIED = 4


# Trust weight per tier — used to scale a finding's contribution to a dimension.
_TIER_WEIGHT: dict[SourceTier, float] = {
    SourceTier.GOVERNMENT: 1.00,
    SourceTier.ESTABLISHED_MEDIA: 0.75,
    SourceTier.INDUSTRY_COMMENTARY: 0.40,
    SourceTier.UNVERIFIED: 0.15,
}

_TIER_LABEL: dict[SourceTier, str] = {
    SourceTier.GOVERNMENT: "Tier 1 — Government / Regulator / Court",
    SourceTier.ESTABLISHED_MEDIA: "Tier 2 — Established media / wire",
    SourceTier.INDUSTRY_COMMENTARY: "Tier 3 — Industry / commentary / company DB",
    SourceTier.UNVERIFIED: "Tier 4 — Forum / UGC / unverified",
}

# ── Tier 1: government, regulators, courts, official registries ──────────────
# Any *.gov.in / *.nic.in / *.gov host is treated as Tier 1 by suffix rule below;
# these are the explicit non-".gov" official portals.
_TIER1_HOSTS: frozenset[str] = frozenset({
    "rbi.org.in",
    "sebi.gov.in",
    "mca.gov.in",
    "gst.gov.in",
    "ibbi.gov.in",
    "nclt.gov.in",
    "nclat.gov.in",
    "ecourts.gov.in",
    "sci.gov.in",
    "drt.gov.in",
    "incometax.gov.in",
    "incometaxindia.gov.in",
    "cbic.gov.in",
    "epfindia.gov.in",
    "esic.gov.in",
    "udyamregistration.gov.in",
    "sfio.nic.in",
    "enforcementdirectorate.gov.in",
    "cbi.gov.in",
    "cybercrime.gov.in",
    "fiuindia.gov.in",
    "mha.gov.in",
    "pib.gov.in",
    # Court judgment primary-source aggregator (full text of orders).
    "indiankanoon.org",
    # Official credit-default / wilful-defaulter registries.
    "suit.cibil.com",
    "watchoutinvestors.com",
    "ibapi.in",
    "npci.org.in",
    # International sanctions / AML primary sources.
    "un.org",
    "sanctionssearch.ofac.treas.gov",
    "treasury.gov",
    "eeas.europa.eu",
    "gov.uk",
    "interpol.int",
    "fatf-gafi.org",
    "opensanctions.org",
})

# Recognised SEBI-registered credit-rating agencies — treated as Tier 1 for
# the credit_ratings dimension because their published rationales are the
# authoritative source for a rating action.
_TIER1_RATING_HOSTS: frozenset[str] = frozenset({
    "crisil.com",
    "icra.in",
    "careratings.com",
    "careedge.in",
    "indiaratings.co.in",
    "brickworkratings.com",
    "acuite.in",
    "infomerics.com",
})

# ── Tier 2: established news wires & national financial press ────────────────
_TIER2_HOSTS: frozenset[str] = frozenset({
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "apnews.com",
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
    "livemint.com",
    "business-standard.com",
    "thehindu.com",
    "thehindubusinessline.com",
    "financialexpress.com",
    "moneycontrol.com",
    "cnbctv18.com",
    "indianexpress.com",
    "hindustantimes.com",
    "ndtv.com",
    "indiatoday.in",
    "theprint.in",
    "news18.com",
    "zeebiz.com",
    "etnownews.com",
    "deccanherald.com",
    "tribuneindia.com",
    "thestatesman.com",
    "newindianexpress.com",
    "wionews.com",
    "scroll.in",
    "thewire.in",
    "barandbench.com",
    "livelaw.in",
})

# ── Tier 3: industry blogs, company databases, niche commentary ──────────────
_TIER3_HOSTS: frozenset[str] = frozenset({
    "tofler.in",
    "zaubacorp.com",
    "tracxn.com",
    "thecompanycheck.com",
    "instafinancials.com",
    "the420.in",
    "inc42.com",
    "entrackr.com",
    "medianama.com",
    "trak.in",
    "bizapprise.com",
    "screener.in",
    "stockmaniacs.net",
    "transparency.org",
    "offshoreleaks.icij.org",
    "aleph.occrp.org",
})

# ── Tier 4: forums / UGC / low-control platforms ─────────────────────────────
_TIER4_HOSTS: frozenset[str] = frozenset({
    "consumercomplaints.in",
    "mouthshut.com",
    "quora.com",
    "reddit.com",
    "scribd.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "youtube.com",
    "medium.com",
    "blogspot.com",
    "wordpress.com",
    "slideshare.net",
})


@dataclass(frozen=True)
class SourceCredibility:
    """Resolved credibility for a single source."""

    tier: SourceTier
    weight: float
    label: str
    host: str

    @property
    def is_official(self) -> bool:
        """True for Tier-1 sources that can substantiate a VERIFIED FACT."""
        return self.tier == SourceTier.GOVERNMENT


def _host_of(url: str) -> str:
    """Best-effort lowercase host without a leading ``www.``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "//" + raw
    try:
        host = urllib.parse.urlparse(raw).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    # strip port
    return host.split(":")[0]


def _host_matches(host: str, allow: frozenset[str]) -> bool:
    """True if host equals or is a subdomain of any allow-listed host."""
    return host in allow or any(host.endswith("." + h) for h in allow)


def classify_source(url: str | None, *, dimension: str | None = None) -> SourceCredibility:
    """Map a URL (or bare host) to its credibility tier.

    ``dimension`` lets credit-rating-agency hosts be promoted to Tier 1 for the
    ``credit_ratings`` dimension (their rating rationales are authoritative
    there) while staying Tier 2/3 elsewhere. This keeps the model general — no
    vendor names are referenced.
    """
    host = _host_of(url or "")
    if not host:
        return SourceCredibility(SourceTier.UNVERIFIED, _TIER_WEIGHT[SourceTier.UNVERIFIED],
                                 _TIER_LABEL[SourceTier.UNVERIFIED], host)

    # Government / official suffix rule covers every *.gov / *.gov.in / *.nic.in.
    if (
        host.endswith(".gov")
        or host.endswith(".gov.in")
        or host.endswith(".nic.in")
        or _host_matches(host, _TIER1_HOSTS)
    ):
        tier = SourceTier.GOVERNMENT
    elif _host_matches(host, _TIER1_RATING_HOSTS):
        tier = SourceTier.GOVERNMENT if dimension == "credit_ratings" else SourceTier.ESTABLISHED_MEDIA
    elif _host_matches(host, _TIER2_HOSTS):
        tier = SourceTier.ESTABLISHED_MEDIA
    elif _host_matches(host, _TIER3_HOSTS):
        tier = SourceTier.INDUSTRY_COMMENTARY
    elif _host_matches(host, _TIER4_HOSTS):
        tier = SourceTier.UNVERIFIED
    else:
        # Unknown domain → least trusted. A generic ".com"/".in" we don't
        # recognise must never be allowed to drive a veto-class conclusion.
        tier = SourceTier.UNVERIFIED

    return SourceCredibility(tier, _TIER_WEIGHT[tier], _TIER_LABEL[tier], host)


def tier_weight(tier: SourceTier) -> float:
    return _TIER_WEIGHT[tier]
