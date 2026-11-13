from __future__ import annotations

import json
import math
import mimetypes
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from no_fly_zones import DATABASE_PATH, distance_meters


CAPTURE_STATUS_PENDING = "PENDING"
CAPTURE_STATUS_CAPTURING = "CAPTURING"
CAPTURE_STATUS_PRIVACY_PROCESSING = "PRIVACY_PROCESSING"
CAPTURE_STATUS_COMPLETED = "COMPLETED"
CAPTURE_STATUS_RETRY_REQUIRED = "RETRY_REQUIRED"
CAPTURE_STATUS_MANUAL_REVIEW = "MANUAL_REVIEW"
CAPTURE_STATUS_FAILED = "FAILED"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_stored_image_path(path: Optional[str], db_path: str = DATABASE_PATH) -> Optional[str]:
    if not path:
        return None

    normalized = os.path.normpath(path)
    if os.path.exists(normalized):
        return normalized

    data_root = os.path.join(os.path.dirname(os.path.abspath(db_path)), "captures")
    normalized_parts = [part for part in normalized.split(os.sep) if part]
    lower_parts = [part.lower() for part in normalized_parts]
    if "captures" in lower_parts:
        captures_index = lower_parts.index("captures")
        relative_parts = normalized_parts[captures_index + 1 :]
        if relative_parts:
            candidate = os.path.normpath(os.path.join(data_root, *relative_parts))
            if os.path.exists(candidate):
                return candidate

    file_name = os.path.basename(normalized)
    if not file_name:
        return None

    for folder_name in ("processed", "original"):
        candidate = os.path.join(data_root, folder_name, file_name)
        if os.path.exists(candidate):
            return os.path.normpath(candidate)

    return normalized


def ensure_inspection_database(db_path: str = DATABASE_PATH) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inspection_images (
                image_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                bin_id INTEGER NOT NULL,
                captured_at TEXT,
                capture_latitude REAL NOT NULL,
                capture_longitude REAL NOT NULL,
                original_image_path TEXT,
                processed_image_path TEXT,
                people_detected INTEGER NOT NULL DEFAULT 0,
                faces_detected INTEGER NOT NULL DEFAULT 0,
                license_plates_detected INTEGER NOT NULL DEFAULT 0,
                moving_vehicle_detected INTEGER NOT NULL DEFAULT 0,
                privacy_processing_status TEXT NOT NULL DEFAULT 'PENDING',
                confidence_score REAL NOT NULL DEFAULT 0,
                manual_review_required INTEGER NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
                retention_expiry_at TEXT,
                capture_status TEXT NOT NULL DEFAULT 'PENDING',
                provider_name TEXT NOT NULL DEFAULT 'simulated-map-provider',
                processing_log_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def get_connection(db_path: str = DATABASE_PATH) -> sqlite3.Connection:
    ensure_inspection_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def row_to_image(row: sqlite3.Row | None) -> Optional[Dict]:
    if row is None:
        return None
    return {
        "image_id": row["image_id"],
        "request_id": row["request_id"],
        "bin_id": row["bin_id"],
        "captured_at": row["captured_at"],
        "capture_latitude": row["capture_latitude"],
        "capture_longitude": row["capture_longitude"],
        "original_image_path": resolve_stored_image_path(row["original_image_path"], DATABASE_PATH),
        "processed_image_path": resolve_stored_image_path(row["processed_image_path"], DATABASE_PATH),
        "people_detected": bool(row["people_detected"]),
        "faces_detected": bool(row["faces_detected"]),
        "license_plates_detected": bool(row["license_plates_detected"]),
        "moving_vehicle_detected": bool(row["moving_vehicle_detected"]),
        "privacy_processing_status": row["privacy_processing_status"],
        "confidence_score": float(row["confidence_score"]),
        "manual_review_required": bool(row["manual_review_required"]),
        "review_status": row["review_status"],
        "retention_expiry_at": row["retention_expiry_at"],
        "capture_status": row["capture_status"],
        "provider_name": row["provider_name"],
        "processing_log": json.loads(row["processing_log_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_image_record(
    request_id: str,
    bin_id: int,
    capture_latitude: float,
    capture_longitude: float,
    provider_name: str,
    db_path: str = DATABASE_PATH,
) -> int:
    now = utc_now_iso()
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO inspection_images (
                request_id, bin_id, capture_latitude, capture_longitude, provider_name,
                capture_status, privacy_processing_status, processing_log_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                int(bin_id),
                float(capture_latitude),
                float(capture_longitude),
                provider_name,
                CAPTURE_STATUS_PENDING,
                CAPTURE_STATUS_PENDING,
                json.dumps({"events": [{"timestamp": now, "status": CAPTURE_STATUS_PENDING}]}),
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def update_image_record(image_id: int, db_path: str = DATABASE_PATH, **fields) -> Optional[Dict]:
    existing = get_image_record(image_id, db_path=db_path)
    if not existing:
        return None
    payload = dict(existing)
    payload.update(fields)
    processing_log = payload.get("processing_log") or {}
    payload["processing_log_json"] = json.dumps(processing_log)
    now = utc_now_iso()

    with get_connection(db_path) as connection:
        connection.execute(
            """
            UPDATE inspection_images
            SET captured_at = ?, original_image_path = ?, processed_image_path = ?,
                people_detected = ?, faces_detected = ?, license_plates_detected = ?,
                moving_vehicle_detected = ?, privacy_processing_status = ?, confidence_score = ?,
                manual_review_required = ?, review_status = ?, retention_expiry_at = ?,
                capture_status = ?, provider_name = ?, processing_log_json = ?, updated_at = ?
            WHERE image_id = ?
            """,
            (
                payload.get("captured_at"),
                payload.get("original_image_path"),
                payload.get("processed_image_path"),
                1 if payload.get("people_detected") else 0,
                1 if payload.get("faces_detected") else 0,
                1 if payload.get("license_plates_detected") else 0,
                1 if payload.get("moving_vehicle_detected") else 0,
                payload.get("privacy_processing_status") or CAPTURE_STATUS_PENDING,
                float(payload.get("confidence_score") or 0),
                1 if payload.get("manual_review_required") else 0,
                payload.get("review_status") or "PENDING_REVIEW",
                payload.get("retention_expiry_at"),
                payload.get("capture_status") or CAPTURE_STATUS_PENDING,
                payload.get("provider_name") or "simulated-map-provider",
                payload["processing_log_json"],
                now,
                image_id,
            ),
        )
        connection.commit()
    return get_image_record(image_id, db_path=db_path)


def append_processing_event(image_id: int, message: str, db_path: str = DATABASE_PATH, **details) -> Optional[Dict]:
    record = get_image_record(image_id, db_path=db_path)
    if not record:
        return None
    processing_log = dict(record.get("processing_log") or {})
    events = list(processing_log.get("events") or [])
    event = {"timestamp": utc_now_iso(), "message": message}
    event.update(details)
    events.append(event)
    processing_log["events"] = events
    return update_image_record(image_id, db_path=db_path, processing_log=processing_log)


def get_image_record(image_id: int, db_path: str = DATABASE_PATH) -> Optional[Dict]:
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM inspection_images WHERE image_id = ?",
            (int(image_id),),
        ).fetchone()
    return row_to_image(row)


def get_latest_image_for_bin(bin_id: int, db_path: str = DATABASE_PATH) -> Optional[Dict]:
    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT * FROM inspection_images
            WHERE bin_id = ?
            ORDER BY image_id DESC
            LIMIT 1
            """,
            (int(bin_id),),
        ).fetchone()
    return row_to_image(row)


def list_images_for_request(request_id: str, db_path: str = DATABASE_PATH) -> List[Dict]:
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM inspection_images WHERE request_id = ? ORDER BY image_id DESC",
            (str(request_id),),
        ).fetchall()
    return [row_to_image(row) for row in rows]


def set_manual_review(image_id: int, required: bool, db_path: str = DATABASE_PATH) -> Optional[Dict]:
    review_status = "MANUAL_REVIEW" if required else "AUTO_APPROVED"
    return update_image_record(
        image_id,
        db_path=db_path,
        manual_review_required=bool(required),
        review_status=review_status,
        capture_status=CAPTURE_STATUS_MANUAL_REVIEW if required else CAPTURE_STATUS_COMPLETED,
    )


def delete_all_images(db_path: str = DATABASE_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute("DELETE FROM inspection_images")
        connection.commit()


def delete_image(image_id: int, db_path: str = DATABASE_PATH) -> Optional[Dict]:
    image = get_image_record(int(image_id), db_path=db_path)
    if not image:
        return None
    for path in {image.get("original_image_path"), image.get("processed_image_path")}:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    with get_connection(db_path) as connection:
        connection.execute("DELETE FROM inspection_images WHERE image_id = ?", (int(image_id),))
        connection.commit()
    return image


def _guess_mime_type(file_name: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or fallback


@dataclass
class CaptureConfig:
    storage_root: str
    db_path: str = DATABASE_PATH
    max_retry_count: int = 2
    retry_interval_seconds: float = 0.15
    allow_original_storage: bool = False
    placeholder_on_failure: bool = True
    retention_days: int = 30
    capture_radius_meters: float = 28.0
    mapillary_access_token: str = ""
    mapillary_request_timeout_seconds: float = 8.0
    mapillary_search_radius_meters: float = 45.0
    prefer_real_imagery: bool = False


class PropertyImageCaptureProvider(ABC):
    name = "provider"

    @abstractmethod
    def capture_property_image(self, bin_item: Dict, survey_context: Dict, attempt: int = 0) -> Dict:
        raise NotImplementedError


class PrivacyProcessor(ABC):
    @abstractmethod
    def detectPeople(self, capture_payload: Dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def detectFaces(self, capture_payload: Dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def detectLicensePlates(self, capture_payload: Dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def detectMovingVehicles(self, capture_payload: Dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def blurSensitiveRegions(self, capture_payload: Dict) -> str:
        raise NotImplementedError

    @abstractmethod
    def validateProcessedImage(self, capture_payload: Dict) -> Dict:
        raise NotImplementedError


def _safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def _split_civic_address(address: str) -> Dict[str, str]:
    text = (address or "").strip()
    if not text:
        return {"number": "?", "street": "Unknown Street", "city": "", "full": "Address unavailable"}
    parts = [part.strip() for part in text.split(",") if part.strip()]
    first = parts[0] if parts else text
    tokens = first.split()
    number = tokens[0] if tokens and any(ch.isdigit() for ch in tokens[0]) else "?"
    street = " ".join(tokens[1:]) if len(tokens) > 1 else first
    return {
        "number": number,
        "street": street or "Unknown Street",
        "city": parts[1] if len(parts) > 1 else "",
        "full": text,
    }


def _property_style_seed(bin_item: Dict) -> int:
    address = str(bin_item.get("address", ""))
    return (sum(ord(ch) for ch in address) + int(bin_item.get("id", 0))) % 4


def _render_base_svg(
    bin_item: Dict,
    address: str,
    property_label: str,
    capture_note: str,
    include_person: bool,
    include_vehicle: bool,
    include_plate: bool,
) -> str:
    bin_color = {
        "green": "#2a9d8f",
        "blue": "#2d6cdf",
        "garbage": "#656d76",
    }.get(str(bin_item.get("bin_type", "garbage")).lower(), "#656d76")
    condition = str(bin_item.get("condition", "standard")).title()
    bin_number = f"#{bin_item.get('id', 'NA')}"
    civic = _split_civic_address(address)
    style_seed = _property_style_seed(bin_item)
    facade_palettes = [
        ("#f4f1de", "#d6ccc2", "#bc6c25", "#9c6644"),
        ("#e9f5db", "#cfe1b9", "#588157", "#3a5a40"),
        ("#edf6f9", "#c9d6df", "#457b9d", "#1d3557"),
        ("#fff1e6", "#e9c46a", "#b56576", "#6d597a"),
    ]
    facade_fill, facade_stroke, door_fill, roof_fill = facade_palettes[style_seed]
    property_title = f"{civic['number']} {civic['street']}".strip()
    person_svg = (
        '<circle cx="470" cy="210" r="18" fill="#f1c27d" />'
        '<rect x="452" y="228" width="36" height="70" rx="12" fill="#264653" />'
    ) if include_person else ""
    vehicle_svg = (
        '<rect x="420" y="300" width="170" height="54" rx="12" fill="#adb5bd" />'
        '<rect x="470" y="316" width="62" height="16" rx="4" fill="#ffffff" stroke="#2f3e46" />'
        '<circle cx="455" cy="357" r="18" fill="#343a40" />'
        '<circle cx="555" cy="357" r="18" fill="#343a40" />'
    ) if include_vehicle else ""
    plate_svg = (
        '<rect x="490" y="316" width="42" height="12" rx="2" fill="#ffffff" stroke="#2f3e46" />'
        '<text x="511" y="325" font-size="7" text-anchor="middle" fill="#2f3e46">PLATE</text>'
    ) if include_vehicle and include_plate else ""
    overflow_badge = (
        '<circle cx="356" cy="266" r="18" fill="#ef476f" />'
        '<text x="356" y="272" font-size="16" font-weight="700" text-anchor="middle" fill="#ffffff">!</text>'
    ) if bin_item.get("is_overflowing") else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <rect width="960" height="540" fill="#dfe7f2" />
  <rect y="350" width="960" height="190" fill="#8d99ae" />
  <rect y="362" width="960" height="14" fill="#f1faee" opacity="0.8" />
  <rect x="90" y="130" width="360" height="220" rx="8" fill="{facade_fill}" stroke="{facade_stroke}" stroke-width="4" />
  <rect x="220" y="190" width="90" height="160" fill="{door_fill}" />
  <polygon points="70,170 270,70 470,170" fill="{roof_fill}" />
  <rect x="128" y="210" width="64" height="52" fill="#ffffff" stroke="#adb5bd" />
  <rect x="320" y="210" width="64" height="52" fill="#ffffff" stroke="#adb5bd" />
  <rect x="116" y="148" width="124" height="36" rx="10" fill="#ffffff" opacity="0.94" />
  <text x="178" y="172" font-size="16" font-weight="700" text-anchor="middle" fill="#1d3557">{property_title}</text>
  <rect x="308" y="278" width="82" height="78" rx="10" fill="#ffffff" stroke="#212529" stroke-width="3" />
  <rect x="316" y="286" width="66" height="60" rx="8" fill="{bin_color}" />
  <rect x="326" y="274" width="48" height="10" rx="4" fill="#212529" />
  <text x="349" y="313" font-size="16" font-weight="700" text-anchor="middle" fill="#ffffff">{bin_number}</text>
  <text x="349" y="333" font-size="12" text-anchor="middle" fill="#ffffff">{condition}</text>
  {overflow_badge}
  {person_svg}
  {vehicle_svg}
  {plate_svg}
  <rect x="62" y="392" width="836" height="104" rx="18" fill="#ffffff" opacity="0.92" />
  <text x="92" y="427" font-size="28" font-weight="700" fill="#1d3557">{property_label}</text>
  <text x="92" y="458" font-size="18" fill="#52627c">{address}</text>
  <text x="92" y="486" font-size="16" fill="#7a8699">{capture_note}</text>
</svg>"""


def _render_photo_evidence_svg(image_data_url: str, address: str, bin_item: Dict, provider_note: str) -> str:
    condition = str(bin_item.get("condition", "standard")).title()
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <rect width="960" height="540" fill="#edf2f7" />
  <image href="{image_data_url}" x="0" y="0" width="960" height="430" preserveAspectRatio="xMidYMid slice" />
  <rect x="0" y="430" width="960" height="110" fill="#ffffff" opacity="0.94" />
  <text x="28" y="470" font-size="28" font-weight="700" fill="#1d3557">{bin_item.get("label") or bin_item.get("bin_type")}</text>
  <text x="28" y="500" font-size="16" fill="#52627c">{address}</text>
  <text x="28" y="524" font-size="14" fill="#7a8699">Condition: {condition} • {provider_note}</text>
</svg>"""

def _render_frontage_photo_evidence_svg(image_data_url: str, address: str, bin_item: Dict, provider_note: str) -> str:
    condition = str(bin_item.get("condition", "standard")).title()
    civic = _split_civic_address(address)
    bin_color = {
        "green": "#2a9d8f",
        "blue": "#2d6cdf",
        "garbage": "#656d76",
    }.get(str(bin_item.get("bin_type", "garbage")).lower(), "#656d76")
    bin_number = f"#{bin_item.get('id', 'NA')}"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <rect width="960" height="540" fill="#edf2f7" />
  <image href="{image_data_url}" x="0" y="0" width="960" height="430" preserveAspectRatio="xMidYMid slice" />
  <rect x="650" y="250" width="122" height="124" rx="16" fill="#ffffff" opacity="0.94" stroke="#1f2937" stroke-width="3" />
  <rect x="664" y="268" width="92" height="90" rx="14" fill="{bin_color}" />
  <rect x="686" y="248" width="48" height="14" rx="5" fill="#111827" />
  <text x="710" y="317" font-size="28" font-weight="700" text-anchor="middle" fill="#ffffff">{bin_number}</text>
  <text x="710" y="343" font-size="14" text-anchor="middle" fill="#ffffff">{condition}</text>
  <rect x="28" y="24" width="280" height="58" rx="14" fill="#ffffff" opacity="0.92" />
  <text x="48" y="60" font-size="24" font-weight="700" fill="#1d3557">{civic['number']} {civic['street']}</text>
  <rect x="0" y="430" width="960" height="110" fill="#ffffff" opacity="0.94" />
  <text x="28" y="470" font-size="28" font-weight="700" fill="#1d3557">{bin_item.get("label") or bin_item.get("bin_type")}</text>
  <text x="28" y="500" font-size="16" fill="#52627c">{address}</text>
  <text x="28" y="524" font-size="14" fill="#7a8699">Condition: {condition} • {provider_note}</text>
</svg>"""


def _mapillary_match_assessment(bin_item: Dict, image_item: Dict, distance_m: float) -> Dict:
    quality_score = float(image_item.get("quality_score") or 0.0)
    compass_angle = image_item.get("compass_angle")
    civic = _split_civic_address(str(bin_item.get("address") or ""))
    score = 1.0
    score -= min(distance_m / 85.0, 0.65)
    if quality_score:
        score = min(1.0, score + min(quality_score, 1.0) * 0.2)
    if compass_angle is None:
        score -= 0.08
    if not civic["street"]:
        score -= 0.08
    score = max(0.0, min(1.0, score))
    if score >= 0.8 and distance_m <= 22:
        level = "EXACT_FRONTAGE_LIKE"
    elif score >= 0.6 and distance_m <= 35:
        level = "NEARBY_FRONTAGE_LIKE"
    else:
        level = "NEEDS_SIMULATED_FRONTAGE"
    return {"score": round(score, 2), "level": level, "distance_m": round(distance_m, 1)}


class MockStreetImageCaptureProvider(PropertyImageCaptureProvider):
    name = "simulated-map-provider"

    def capture_property_image(self, bin_item: Dict, survey_context: Dict, attempt: int = 0) -> Dict:
        location_importance = str(bin_item.get("location_importance", "residential") or "residential").lower()
        include_person = location_importance in {"school", "hospital", "pedestrian", "transit"} or int(bin_item.get("id", 0)) % 2 == 1
        include_vehicle = int(bin_item.get("id", 0)) % 3 == 0
        include_plate = include_vehicle
        moving_vehicle_attempts = int(bin_item.get("simulated_moving_vehicle_attempts", 0))
        moving_vehicle_detected = attempt < moving_vehicle_attempts
        property_label = {
            "school": "School frontage",
            "hospital": "Hospital frontage",
            "transit": "Transit-adjacent property",
            "pedestrian": "Pedestrian zone property",
        }.get(location_importance, "Street-facing property frontage")
        address = bin_item.get("address") or f"{bin_item.get('lat', 0):.5f}, {bin_item.get('lon', 0):.5f}"
        capture_note = (
            "Simulated street-level evidence capture during drone survey."
            if not moving_vehicle_detected
            else "Moving vehicle detected in capture area. Retry required."
        )
        confidence = max(0.62, min(0.97, 0.74 + ((int(bin_item.get("id", 0)) % 5) * 0.05)))
        svg_markup = _render_base_svg(
            bin_item,
            address=address,
            property_label=property_label,
            capture_note=capture_note,
            include_person=include_person,
            include_vehicle=include_vehicle,
            include_plate=include_plate,
        )
        sensitive_regions = []
        if include_person:
            sensitive_regions.append({"type": "face", "x": 452, "y": 192, "width": 36, "height": 36})
            sensitive_regions.append({"type": "person", "x": 448, "y": 192, "width": 44, "height": 110})
        if include_vehicle and include_plate:
            sensitive_regions.append({"type": "license_plate", "x": 488, "y": 312, "width": 48, "height": 20})

        return {
            "provider_name": self.name,
            "svg_markup": svg_markup,
            "bin_item": dict(bin_item),
            "survey_context": dict(survey_context),
            "people_detected": include_person,
            "faces_detected": include_person,
            "license_plates_detected": include_vehicle and include_plate,
            "moving_vehicle_detected": moving_vehicle_detected,
            "sensitive_regions": sensitive_regions,
            "confidence_score": round(confidence, 2),
            "capture_note": capture_note,
            "fallback_used": False,
        }


class MapillaryStreetImageCaptureProvider(PropertyImageCaptureProvider):
    name = "mapillary"

    def __init__(self, access_token: str, timeout_seconds: float = 8.0, search_radius_meters: float = 45.0) -> None:
        self.access_token = access_token.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.search_radius_meters = float(search_radius_meters)

    def _request_json(self, url: str) -> Dict:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "DroneSimHackathon/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "DroneSimHackathon/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()

    def _build_bbox(self, lat: float, lon: float) -> str:
        lat_delta = self.search_radius_meters / 111_320.0
        lon_delta = self.search_radius_meters / (111_320.0 * max(0.1, abs(math.cos(math.radians(lat)))))
        west = lon - lon_delta
        south = lat - lat_delta
        east = lon + lon_delta
        north = lat + lat_delta
        return f"{west},{south},{east},{north}"

    def _choose_best_image(self, data: List[Dict], lat: float, lon: float) -> Optional[Dict]:
        best = None
        best_distance = None
        for item in data:
            geometry = item.get("computed_geometry") or item.get("geometry") or {}
            coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
            if not coordinates or len(coordinates) < 2:
                continue
            item_point = {"lat": float(coordinates[1]), "lon": float(coordinates[0])}
            current_distance = distance_meters({"lat": lat, "lon": lon}, item_point)
            if best is None or current_distance < best_distance:
                best = item
                best_distance = current_distance
        return best

    def capture_property_image(self, bin_item: Dict, survey_context: Dict, attempt: int = 0) -> Dict:
        if not self.access_token:
            raise RuntimeError("Mapillary access token is not configured.")

        lat = float(bin_item["lat"])
        lon = float(bin_item["lon"])
        fields = "id,captured_at,thumb_1024_url,thumb_2048_url,geometry,computed_geometry,compass_angle,quality_score"
        query = urllib.parse.urlencode(
            {
                "access_token": self.access_token,
                "fields": fields,
                "bbox": self._build_bbox(lat, lon),
                "limit": 6,
            }
        )
        search_url = f"https://graph.mapillary.com/images?{query}"
        try:
            response = self._request_json(search_url)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Mapillary search failed: {exc}") from exc
        data = response.get("data") or []
        chosen = self._choose_best_image(data, lat, lon)
        if not chosen:
            raise RuntimeError("No nearby Mapillary imagery was found for this bin address.")
        geometry = chosen.get("computed_geometry") or chosen.get("geometry") or {}
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        image_point = {"lat": float(coordinates[1]), "lon": float(coordinates[0])} if coordinates and len(coordinates) >= 2 else {"lat": lat, "lon": lon}
        match = _mapillary_match_assessment(bin_item, chosen, distance_meters({"lat": lat, "lon": lon}, image_point))

        image_url = chosen.get("thumb_2048_url") or chosen.get("thumb_1024_url")
        if not image_url:
            raise RuntimeError("Mapillary image metadata did not include a usable thumbnail URL.")
        try:
            image_bytes = self._request_bytes(image_url)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Mapillary image download failed: {exc}") from exc

        image_data_url = f"data:image/jpeg;base64,{b64encode(image_bytes).decode('ascii')}"
        address = bin_item.get("address") or f"{lat:.5f}, {lon:.5f}"
        if match["level"] == "NEEDS_SIMULATED_FRONTAGE":
            svg_markup = _render_base_svg(
                bin_item,
                address=address,
                property_label=f"{_split_civic_address(address)['number']} {_split_civic_address(address)['street']} frontage",
                capture_note=f"Mapillary nearby image was not frontage-aligned enough. Using civic-frontage simulation. Match score {match['score']}.",
                include_person=False,
                include_vehicle=False,
                include_plate=False,
            )
            provider_name = "mapillary-frontage-fallback"
            capture_note = "Civic-frontage simulation generated from the closest civic address."
        else:
            svg_markup = _render_frontage_photo_evidence_svg(
                image_data_url=image_data_url,
                address=address,
                bin_item=bin_item,
                provider_note=f"Mapillary frontage match {match['level']} ({match['score']})",
            )
            provider_name = self.name
            capture_note = f"Mapillary image captured with {match['level']} confidence."
        include_person = str(bin_item.get("location_importance", "")).lower() in {"school", "hospital", "pedestrian", "transit"}
        moving_vehicle_detected = attempt < int(bin_item.get("simulated_moving_vehicle_attempts", 0))
        return {
            "provider_name": provider_name,
            "svg_markup": svg_markup,
            "bin_item": dict(bin_item),
            "survey_context": dict(survey_context),
            "people_detected": include_person,
            "faces_detected": include_person,
            "license_plates_detected": False,
            "moving_vehicle_detected": moving_vehicle_detected,
            "sensitive_regions": [{"type": "person", "x": 620, "y": 120, "width": 120, "height": 220}] if include_person else [],
            "confidence_score": match["score"],
            "capture_note": capture_note,
            "fallback_used": provider_name != self.name,
            "source_image_id": chosen.get("id"),
            "source_captured_at": chosen.get("captured_at"),
            "frontage_match": match,
        }


class PlaceholderImageCaptureProvider(PropertyImageCaptureProvider):
    name = "placeholder-provider"

    def capture_property_image(self, bin_item: Dict, survey_context: Dict, attempt: int = 0) -> Dict:
        address = bin_item.get("address") or "Address unavailable"
        svg_markup = f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
  <rect width="960" height="540" fill="#edf2f7" />
  <rect x="70" y="90" width="820" height="360" rx="24" fill="#ffffff" stroke="#d8e2ec" stroke-width="4" />
  <text x="120" y="180" font-size="40" font-weight="700" fill="#1d3557">Placeholder Evidence Image</text>
  <text x="120" y="238" font-size="22" fill="#52627c">{address}</text>
  <text x="120" y="288" font-size="18" fill="#7a8699">Automated capture was unavailable. Placeholder evidence retained for workflow continuity.</text>
  <text x="120" y="336" font-size="18" fill="#7a8699">Bin: {bin_item.get("label") or bin_item.get("bin_type")}</text>
</svg>"""
        return {
            "provider_name": self.name,
            "svg_markup": svg_markup,
            "bin_item": dict(bin_item),
            "survey_context": dict(survey_context),
            "people_detected": False,
            "faces_detected": False,
            "license_plates_detected": False,
            "moving_vehicle_detected": False,
            "sensitive_regions": [],
            "confidence_score": 0.55,
            "capture_note": "Placeholder imagery used.",
            "fallback_used": True,
        }


class MockPrivacyProcessor(PrivacyProcessor):
    def detectPeople(self, capture_payload: Dict) -> bool:
        return bool(capture_payload.get("people_detected"))

    def detectFaces(self, capture_payload: Dict) -> bool:
        return bool(capture_payload.get("faces_detected"))

    def detectLicensePlates(self, capture_payload: Dict) -> bool:
        return bool(capture_payload.get("license_plates_detected"))

    def detectMovingVehicles(self, capture_payload: Dict) -> bool:
        return bool(capture_payload.get("moving_vehicle_detected"))

    def blurSensitiveRegions(self, capture_payload: Dict) -> str:
        svg_markup = capture_payload["svg_markup"]
        overlays = []
        for region in capture_payload.get("sensitive_regions", []):
            overlays.append(
                f'<rect x="{region["x"]}" y="{region["y"]}" width="{region["width"]}" height="{region["height"]}" '
                'fill="#ced4da" opacity="0.86" rx="6" />'
            )
            overlays.append(
                f'<text x="{region["x"] + (region["width"] / 2)}" y="{region["y"] + (region["height"] / 2) + 4}" '
                'font-size="10" text-anchor="middle" fill="#343a40">redacted</text>'
            )
        if not overlays:
            return svg_markup
        return svg_markup.replace("</svg>", "".join(overlays) + "</svg>")

    def validateProcessedImage(self, capture_payload: Dict) -> Dict:
        sensitive_regions = capture_payload.get("sensitive_regions", [])
        needs_review = bool(capture_payload.get("moving_vehicle_detected")) or float(capture_payload.get("confidence_score", 0)) < 0.7
        return {
            "valid": True,
            "manual_review_required": needs_review,
            "review_status": "MANUAL_REVIEW" if needs_review else "AUTO_APPROVED",
            "redacted_region_count": len(sensitive_regions),
        }


class SurveyInspectionImageService:
    def __init__(
        self,
        config: CaptureConfig,
        provider: Optional[PropertyImageCaptureProvider] = None,
        placeholder_provider: Optional[PropertyImageCaptureProvider] = None,
        privacy_processor: Optional[PrivacyProcessor] = None,
    ) -> None:
        self.config = config
        self.provider = provider or MockStreetImageCaptureProvider()
        self.placeholder_provider = placeholder_provider or PlaceholderImageCaptureProvider()
        self.privacy_processor = privacy_processor or MockPrivacyProcessor()
        self.original_dir = os.path.join(self.config.storage_root, "captures", "original")
        self.processed_dir = os.path.join(self.config.storage_root, "captures", "processed")
        os.makedirs(self.original_dir, exist_ok=True)
        os.makedirs(self.processed_dir, exist_ok=True)
        ensure_inspection_database(self.config.db_path)

    def capture_needed_for_bin(self, bin_item: Dict, waypoint: Dict) -> bool:
        return distance_meters(
            {"lat": float(waypoint["lat"]), "lon": float(waypoint["lon"])},
            {"lat": float(bin_item["lat"]), "lon": float(bin_item["lon"])},
        ) <= float(self.config.capture_radius_meters)

    def _write_svg(self, directory: str, file_name: str, svg_markup: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{file_name}.svg")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(svg_markup)
        return path

    def attach_demo_override_image(
        self,
        bin_item: Dict,
        file_name: str,
        file_bytes: bytes,
        mime_type: Optional[str] = None,
        provider_name: str = "demo-upload-provider",
    ) -> Dict:
        request_id = str(bin_item.get("request_id") or f"BIN-{bin_item.get('id', 'UNKNOWN')}")
        image_id = create_image_record(
            request_id=request_id,
            bin_id=int(bin_item["id"]),
            capture_latitude=float(bin_item["lat"]),
            capture_longitude=float(bin_item["lon"]),
            provider_name=provider_name,
            db_path=self.config.db_path,
        )
        safe_name = _safe_label(os.path.splitext(file_name or "demo_image")[0]) or f"bin_{bin_item['id']}"
        effective_mime = mime_type or _guess_mime_type(file_name or "", "image/jpeg")
        image_data_url = f"data:{effective_mime};base64,{b64encode(file_bytes).decode('ascii')}"
        address = bin_item.get("address") or f"{bin_item.get('lat', 0):.5f}, {bin_item.get('lon', 0):.5f}"
        svg_markup = _render_frontage_photo_evidence_svg(
            image_data_url=image_data_url,
            address=address,
            bin_item=bin_item,
            provider_note="Local demo override image",
        )
        processed_path = self._write_svg(self.processed_dir, f"{safe_name}_{image_id}", svg_markup)
        record = update_image_record(
            image_id,
            db_path=self.config.db_path,
            captured_at=utc_now_iso(),
            original_image_path=None,
            processed_image_path=processed_path,
            people_detected=False,
            faces_detected=False,
            license_plates_detected=False,
            moving_vehicle_detected=False,
            privacy_processing_status="PROCESSED",
            confidence_score=1.0,
            manual_review_required=False,
            review_status="DEMO_OVERRIDE",
            retention_expiry_at=(datetime.now(timezone.utc) + timedelta(days=self.config.retention_days)).replace(microsecond=0).isoformat(),
            capture_status=CAPTURE_STATUS_COMPLETED,
            provider_name=provider_name,
            processing_log={
                "events": [
                    {
                        "timestamp": utc_now_iso(),
                        "message": "Local demo override image attached.",
                        "file_name": file_name,
                        "mime_type": effective_mime,
                    }
                ]
            },
        )
        return record

    def capture_bin_evidence(self, bin_item: Dict, survey_context: Dict) -> Dict:
        request_id = str(bin_item.get("request_id") or f"BIN-{bin_item.get('id', 'UNKNOWN')}")
        image_id = create_image_record(
            request_id=request_id,
            bin_id=int(bin_item["id"]),
            capture_latitude=float(bin_item["lat"]),
            capture_longitude=float(bin_item["lon"]),
            provider_name=self.provider.name,
            db_path=self.config.db_path,
        )
        append_processing_event(image_id, "Inspection image capture requested.", db_path=self.config.db_path)

        payload = None
        moving_vehicle_detected_observed = False
        for attempt in range(self.config.max_retry_count + 1):
            update_image_record(
                image_id,
                db_path=self.config.db_path,
                capture_status=CAPTURE_STATUS_CAPTURING,
                privacy_processing_status=CAPTURE_STATUS_PENDING,
            )
            append_processing_event(image_id, "Capturing image.", attempt=attempt + 1, db_path=self.config.db_path)
            try:
                payload = self.provider.capture_property_image(bin_item, survey_context, attempt=attempt)
            except Exception as exc:
                append_processing_event(
                    image_id,
                    "Primary imagery provider failed.",
                    attempt=attempt + 1,
                    error=str(exc),
                    db_path=self.config.db_path,
                )
                if self.config.placeholder_on_failure:
                    payload = self.placeholder_provider.capture_property_image(bin_item, survey_context, attempt=attempt)
                    append_processing_event(
                        image_id,
                        "Placeholder imagery used because the primary provider was unavailable.",
                        db_path=self.config.db_path,
                    )
                    break
                return update_image_record(
                    image_id,
                    db_path=self.config.db_path,
                    capture_status=CAPTURE_STATUS_FAILED,
                    privacy_processing_status=CAPTURE_STATUS_FAILED,
                    manual_review_required=True,
                    review_status="MANUAL_REVIEW",
                )
            if self.privacy_processor.detectMovingVehicles(payload):
                moving_vehicle_detected_observed = True
                update_image_record(
                    image_id,
                    db_path=self.config.db_path,
                    capture_status=CAPTURE_STATUS_RETRY_REQUIRED,
                    moving_vehicle_detected=True,
                    confidence_score=float(payload.get("confidence_score", 0)),
                )
                append_processing_event(
                    image_id,
                    "Moving vehicle detected. Delaying capture and retrying.",
                    attempt=attempt + 1,
                    db_path=self.config.db_path,
                )
                if attempt < self.config.max_retry_count:
                    time.sleep(self.config.retry_interval_seconds)
                    continue
                if self.config.placeholder_on_failure:
                    payload = self.placeholder_provider.capture_property_image(bin_item, survey_context, attempt=attempt)
                    append_processing_event(
                        image_id,
                        "Fallback placeholder image used after capture retries were exhausted.",
                        db_path=self.config.db_path,
                    )
                else:
                    return update_image_record(
                        image_id,
                        db_path=self.config.db_path,
                        capture_status=CAPTURE_STATUS_FAILED,
                        privacy_processing_status=CAPTURE_STATUS_FAILED,
                        moving_vehicle_detected=True,
                        manual_review_required=True,
                        review_status="MANUAL_REVIEW",
                    )
            break

        update_image_record(
            image_id,
            db_path=self.config.db_path,
            capture_status=CAPTURE_STATUS_PRIVACY_PROCESSING,
            privacy_processing_status=CAPTURE_STATUS_PRIVACY_PROCESSING,
        )
        append_processing_event(image_id, "Privacy processing started.", db_path=self.config.db_path)

        original_path = None
        file_stub = f"bin_{bin_item['id']}_image_{image_id}"
        if self.config.allow_original_storage:
            original_path = self._write_svg(self.original_dir, file_stub, payload["svg_markup"])

        processed_svg = self.privacy_processor.blurSensitiveRegions(payload)
        processed_path = self._write_svg(self.processed_dir, file_stub, processed_svg)
        validation = self.privacy_processor.validateProcessedImage(payload)
        review_status = validation.get("review_status") or ("MANUAL_REVIEW" if validation.get("manual_review_required") else "AUTO_APPROVED")
        capture_status = CAPTURE_STATUS_MANUAL_REVIEW if validation.get("manual_review_required") else CAPTURE_STATUS_COMPLETED
        retention_expiry = (datetime.now(timezone.utc) + timedelta(days=self.config.retention_days)).replace(microsecond=0).isoformat()

        record = update_image_record(
            image_id,
            db_path=self.config.db_path,
            captured_at=utc_now_iso(),
            original_image_path=original_path,
            processed_image_path=processed_path,
            people_detected=self.privacy_processor.detectPeople(payload),
            faces_detected=self.privacy_processor.detectFaces(payload),
            license_plates_detected=self.privacy_processor.detectLicensePlates(payload),
            moving_vehicle_detected=moving_vehicle_detected_observed or self.privacy_processor.detectMovingVehicles(payload),
            privacy_processing_status="PROCESSED",
            confidence_score=float(payload.get("confidence_score", 0)),
            manual_review_required=bool(validation.get("manual_review_required")),
            review_status=review_status,
            retention_expiry_at=retention_expiry,
            capture_status=capture_status,
            provider_name=payload.get("provider_name") or self.provider.name,
        )
        append_processing_event(
            image_id,
            "Privacy processing finished.",
            redacted_region_count=validation.get("redacted_region_count", 0),
            fallback_used=bool(payload.get("fallback_used")),
            db_path=self.config.db_path,
        )
        return record


def attach_latest_images(items: Iterable[Dict], db_path: str = DATABASE_PATH) -> List[Dict]:
    enriched = []
    for item in items:
        copy = dict(item)
        copy["latest_image"] = get_latest_image_for_bin(int(item["id"]), db_path=db_path)
        enriched.append(copy)
    return enriched
