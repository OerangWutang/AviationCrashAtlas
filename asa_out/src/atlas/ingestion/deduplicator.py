"""Duplicate detection — spatial-temporal and fuzzy matching."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass
class DuplicateCandidate:
    event_id_a: str
    event_id_b: str
    match_type: str
    match_score: float
    match_fields: list[str]
    auto_merge: bool


class DuplicateDetector:
    AUTO_MERGE_THRESHOLD = 0.97

    def find_candidates(
        self, candidate: dict[str, Any], existing: list[dict[str, Any]]
    ) -> list[DuplicateCandidate]:
        return sorted(
            [d for d in (self._compare(candidate, e) for e in existing) if d],
            key=lambda d: d.match_score,
            reverse=True,
        )

    def _compare(self, a: dict[str, Any], b: dict[str, Any]) -> DuplicateCandidate | None:
        matched: list[str] = []
        score = 0.0

        # Exact NTSB ID
        id_a, id_b = a.get("ntsb_event_id", ""), b.get("ntsb_event_id", "")
        if id_a and id_b and id_a.upper() == id_b.upper():
            return DuplicateCandidate(a["event_id"], b["event_id"], "exact", 1.0, ["ntsb_event_id"], True)

        # Date proximity
        da, db = a.get("occurred_at"), b.get("occurred_at")
        date_score = 0.0
        if da and db:
            delta = abs((da - db).days)
            if delta == 0:
                date_score = 1.0; matched.append("date_exact")
            elif delta <= 1:
                date_score = 0.8; matched.append("date_±1day")
            elif delta <= 7:
                date_score = 0.4; matched.append("date_±7days")
        if date_score == 0.0:
            return None
        score += date_score * 0.30

        # Spatial
        lata, lona = a.get("latitude"), a.get("longitude")
        latb, lonb = b.get("latitude"), b.get("longitude")
        if all(v is not None for v in (lata, lona, latb, lonb)):
            dist = ((lata - latb) ** 2 + (lona - lonb) ** 2) ** 0.5
            if dist < 0.1:
                score += 0.30; matched.append("location_11km")
            elif dist < 0.5:
                score += 0.15; matched.append("location_55km")

        # Aircraft make
        ma, mb = _norm(a.get("aircraft_make", "")), _norm(b.get("aircraft_make", ""))
        if ma and mb:
            if ma == mb:
                score += 0.20; matched.append("aircraft_make")
                moda, modb = _norm(a.get("aircraft_model", "")), _norm(b.get("aircraft_model", ""))
                if moda and modb and moda == modb:
                    score += 0.10; matched.append("aircraft_model")
            elif _overlap(ma, mb) > 0.6:
                score += 0.10; matched.append("aircraft_make_partial")

        # Registration — normalize punctuation/case/country prefix quirks.
        ra, rb = _norm_registration(a.get("aircraft_registration", "")), _norm_registration(b.get("aircraft_registration", ""))
        if ra and rb and ra == rb:
            score += 0.25; matched.append("registration")

        # Operator / location text fuzzy overlap. These are transparent scoring
        # signals surfaced in duplicate_candidates.match_reasons. They are not
        # used alone; date proximity is still mandatory above.
        oa, ob = _norm(a.get("operator_name", "")), _norm(b.get("operator_name", ""))
        if oa and ob and _overlap(oa, ob) > 0.7:
            score += 0.10; matched.append("operator_name")

        la, lb = _norm_location(a.get("location_text", "")), _norm_location(b.get("location_text", ""))
        if la and lb and _overlap(la, lb) > 0.6:
            score += 0.10; matched.append("location_text")

        # Fatalities
        fa, fb = a.get("fatalities_total"), b.get("fatalities_total")
        if fa and fb and fa > 0 and fa == fb:
            score += 0.10; matched.append("fatalities_total")

        if score < 0.50:
            return None

        match_type = "spatial_temporal" if any(
            "location" in m for m in matched
        ) else "fuzzy"

        return DuplicateCandidate(
            a["event_id"], b["event_id"], match_type,
            round(score, 3), matched, score >= self.AUTO_MERGE_THRESHOLD,
        )


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()



def _norm_registration(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s)).lower()).upper()


def _norm_location(s: str) -> str:
    text = _norm(str(s))
    aliases = {
        "intl": "international",
        "int l": "international",
        "apt": "airport",
        "ap": "airport",
        "afb": "air force base",
    }
    tokens = [aliases.get(tok, tok) for tok in text.split()]
    return " ".join(tokens)


def _overlap(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
