from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional


LOCATION_IMPORTANCE_SCORES = {
    "residential": 0,
    "pedestrian": 10,
    "transit": 18,
    "school": 22,
    "hospital": 24,
}


PRIORITY_BANDS = (
    ("CRITICAL", 120),
    ("HIGH", 80),
    ("MEDIUM", 45),
    ("NORMAL", 0),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hours_since(value: Optional[str], now: Optional[datetime] = None) -> float:
    parsed = parse_timestamp(value)
    if parsed is None:
        return 0.0
    current = now or datetime.now(timezone.utc)
    delta = current - parsed
    return max(0.0, delta.total_seconds() / 3600.0)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def default_location_importance(address: str) -> str:
    text = (address or "").lower()
    if any(token in text for token in ("school", "public school", "academy")):
        return "school"
    if any(token in text for token in ("hospital", "clinic", "medical")):
        return "hospital"
    if any(token in text for token in ("transit", "station", "terminal", "bus")):
        return "transit"
    if any(token in text for token in ("park", "square", "mall", "market", "pedestrian")):
        return "pedestrian"
    return "residential"


def calculate_priority(bin_item: Dict, now: Optional[datetime] = None) -> Dict:
    """
    Compute reusable dispatch priority fields for a surveyed bin.

    Formula overview:
    - Base priority:
      - Overflowing bins start at HIGH with a score of 82.
      - Standard bins start at NORMAL with a score of 20.
    - Adjustments:
      - Bin category can slightly raise or lower urgency.
      - Time since last pickup increases urgency gradually.
      - Time since the current request was created increases urgency.
      - High-importance locations such as schools, hospitals, transit,
        and pedestrian-heavy zones add urgency.
      - Existing unresolved requests increase urgency, but duplicate open
        requests are avoided by reusing the existing request identity.
      - Lower inspection confidence slightly reduces urgency.
      - Loose garbage around the bin significantly increases urgency.
    - Priority bands:
      - NORMAL: score < 45
      - MEDIUM: 45-79
      - HIGH: 80-119
      - CRITICAL: 120+
    """

    current = now or datetime.now(timezone.utc)
    is_overflowing = bool(bin_item.get("is_overflowing", False))
    bin_type = str(bin_item.get("bin_type", "garbage")).lower()
    condition = str(bin_item.get("condition") or ("overflowing" if is_overflowing else "standard")).lower()
    location_importance = str(
        bin_item.get("location_importance")
        or default_location_importance(bin_item.get("address", ""))
    ).lower()
    confidence = float(bin_item.get("inspection_confidence", 0.9))
    confidence = clamp(confidence, 0.0, 1.0)
    has_loose_garbage = bool(bin_item.get("loose_garbage_detected", False))
    unresolved_request = bool(bin_item.get("has_open_request", False))

    base_score = 82 if is_overflowing else 20
    category_adjustment = {
        "garbage": 8,
        "green": 4,
        "blue": 2,
    }.get(bin_type, 0)
    condition_adjustment = {
        "overflowing": 10,
        "needs_collection_soon": 24,
        "standard": 0,
    }.get(condition, 0)
    pickup_adjustment = clamp(hours_since(bin_item.get("last_pickup_at"), current) / 12.0, 0, 24)
    request_age_adjustment = clamp(hours_since(bin_item.get("request_created_at"), current) / 6.0, 0, 24)
    location_adjustment = LOCATION_IMPORTANCE_SCORES.get(location_importance, 0)
    unresolved_adjustment = 12 if unresolved_request else 0
    confidence_adjustment = -round((1.0 - confidence) * 18, 2)
    loose_garbage_adjustment = 22 if has_loose_garbage else 0

    score = (
        base_score
        + category_adjustment
        + condition_adjustment
        + pickup_adjustment
        + request_age_adjustment
        + location_adjustment
        + unresolved_adjustment
        + confidence_adjustment
        + loose_garbage_adjustment
    )
    priority_score = int(round(score))

    priority_level = "NORMAL"
    for level, threshold in PRIORITY_BANDS:
        if priority_score >= threshold:
            priority_level = level
            break

    if is_overflowing and location_importance in {"school", "hospital", "transit"}:
        priority_level = "CRITICAL"
        priority_score = max(priority_score, 120)
    if is_overflowing and has_loose_garbage:
        priority_level = "CRITICAL"
        priority_score = max(priority_score, 124)
    if not is_overflowing and priority_level == "NORMAL" and priority_score >= 45:
        priority_level = "MEDIUM"

    request_id = bin_item.get("request_id") or f"BIN-{bin_item.get('id', 'UNKNOWN')}"
    open_request_id = bin_item.get("open_request_id") or (request_id if unresolved_request else None)

    return {
        "condition": condition,
        "priority_level": priority_level,
        "priority_score": priority_score,
        "location_importance": location_importance,
        "inspection_confidence": confidence,
        "has_open_request": unresolved_request,
        "open_request_id": open_request_id,
        "request_id": request_id,
        "loose_garbage_detected": has_loose_garbage,
        "priority_factors": {
            "base_score": base_score,
            "category_adjustment": category_adjustment,
            "condition_adjustment": condition_adjustment,
            "time_since_last_pickup_hours": round(hours_since(bin_item.get("last_pickup_at"), current), 1),
            "time_since_request_creation_hours": round(hours_since(bin_item.get("request_created_at"), current), 1),
            "location_importance": location_importance,
            "location_adjustment": location_adjustment,
            "existing_unresolved_request": unresolved_request,
            "inspection_confidence": confidence,
            "loose_garbage_detected": has_loose_garbage,
        },
    }


def prioritize_dispatch_items(items: Iterable[Dict], now: Optional[datetime] = None) -> List[Dict]:
    prioritized = []
    seen_open_requests = set()
    current = now or datetime.now(timezone.utc)

    for item in items:
        enriched = dict(item)
        enriched.update(calculate_priority(enriched, current))
        open_request_id = enriched.get("open_request_id")
        if open_request_id:
            if open_request_id in seen_open_requests:
                enriched["duplicate_open_request"] = True
            else:
                seen_open_requests.add(open_request_id)
                enriched["duplicate_open_request"] = False
        else:
            enriched["duplicate_open_request"] = False
        prioritized.append(enriched)

    prioritized.sort(
        key=lambda item: (
            -int(item.get("priority_score", 0)),
            parse_timestamp(item.get("request_created_at")) or datetime.max.replace(tzinfo=timezone.utc),
        )
    )
    return prioritized
