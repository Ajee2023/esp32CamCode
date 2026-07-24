import math
import os
import json
import sqlite3
import threading
import time
from base64 import b64decode

from flask import Flask, jsonify, render_template, request, send_file

from drone_control import (
    flight_progress,
    fly_to,
    get_multirotor_state,
    get_position,
    get_speed_multiplier,
    set_sim_weather,
    set_speed_multiplier,
    takeoff,
)
from no_fly_zones import (
    DATABASE_PATH,
    create_zone,
    delete_zone,
    ensure_database,
    get_zone,
    list_zones,
    set_zone_active,
    update_zone,
    validate_route_plan,
    validate_zone_payload,
)
from inspection_pipeline import (
    CaptureConfig,
    MapillaryStreetImageCaptureProvider,
    SurveyInspectionImageService,
    delete_image,
    _guess_mime_type,
    attach_latest_images,
    ensure_inspection_database,
    resolve_stored_image_path,
    get_image_record,
    get_latest_image_for_bin,
    set_manual_review,
)
from priority_service import calculate_priority, prioritize_dispatch_items, utc_now_iso

app = Flask(__name__)

state_lock = threading.Lock()

HOME_POINT = {"lat": 43.7315, "lon": -79.7624}
DRONE_ID = "DRONE-001"
DATA_ROOT = os.path.join(os.path.dirname(__file__), "data")
MAPILLARY_ACCESS_TOKEN = os.environ.get("MAPILLARY_ACCESS_TOKEN", "").strip()

nav_state = {"status": "idle", "message": ""}

zone_mission_state = {
    "status": "idle",
    "message": "No zone survey has been started.",
    "completed_waypoints": 0,
    "total_waypoints": 0,
    "started_at": "",
    "drone_id": DRONE_ID,
}

bin_state = {
    "next_id": 1,
    "bins": [],
    "last_zone": None,
    "last_summary": None,
}

weather_state = {"rain": 0, "wind": 0, "snow": 0}
demo_state = {"speed_multiplier": get_speed_multiplier()}

feedback_state = {
    "cycle": 0,
    "stage": "repeat",
    "safe": True,
    "reason": "Awaiting weather feedback loop.",
    "last_reroute": None,
    "reroute_count": 0,
    "active_route": [],
    "sample_input": {
        "origin": dict(HOME_POINT),
        "destination": {"lat": 43.7365, "lon": -79.7545},
        "weather": dict(weather_state),
    },
}

flight_plan_state = {"route_points": [], "warnings": [], "message": ""}
mission_state = {"active_id": 0}
inspection_config = CaptureConfig(
    storage_root=DATA_ROOT,
    mapillary_access_token=MAPILLARY_ACCESS_TOKEN,
    prefer_real_imagery=bool(MAPILLARY_ACCESS_TOKEN),
)
inspection_provider = (
    MapillaryStreetImageCaptureProvider(
        access_token=inspection_config.mapillary_access_token,
        timeout_seconds=inspection_config.mapillary_request_timeout_seconds,
        search_radius_meters=inspection_config.mapillary_search_radius_meters,
    )
    if inspection_config.prefer_real_imagery
    else None
)
inspection_service = SurveyInspectionImageService(
    inspection_config,
    provider=inspection_provider,
)


def slugify_request_id(value):
    text = "".join(ch if str(ch).isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().upper())
    text = "-".join(part for part in text.split("-") if part)
    return text or "BIN"


def ensure_unique_request_id(base_request_id=None, bin_id=None, exclude_bin_id=None):
    base = slugify_request_id(base_request_id or f"BIN-{bin_id or 'UNKNOWN'}")
    with state_lock:
        used = {
            str(item.get("request_id") or "").strip().upper()
            for item in bin_state["bins"]
            if exclude_bin_id is None or int(item.get("id", -1)) != int(exclude_bin_id)
        }
    if base not in used:
        return base
    suffix = int(bin_id or 0)
    candidate = f"{base}-{suffix}" if suffix else f"{base}-1"
    counter = 1
    while candidate in used:
        counter += 1
        candidate = f"{base}-{suffix or counter}-{counter}"
    return candidate


def ensure_unique_request_id_from_used(used_request_ids, base_request_id=None, bin_id=None):
    base = slugify_request_id(base_request_id or f"BIN-{bin_id or 'UNKNOWN'}")
    used = {str(value or "").strip().upper() for value in used_request_ids if str(value or "").strip()}
    if base not in used:
        return base
    suffix = int(bin_id or 0)
    candidate = f"{base}-{suffix}" if suffix else f"{base}-1"
    counter = 1
    while candidate in used:
        counter += 1
        candidate = f"{base}-{suffix or counter}-{counter}"
    return candidate


def next_bin_id_seed():
    ensure_database()
    ensure_inspection_database()
    ensure_dispatch_history_database()
    max_bin_id = 0
    with get_dispatch_db_connection() as connection:
        dispatch_max = connection.execute("SELECT COALESCE(MAX(bin_id), 0) AS max_bin_id FROM dispatch_entries").fetchone()
        image_max = connection.execute("SELECT COALESCE(MAX(bin_id), 0) AS max_bin_id FROM inspection_images").fetchone()
        max_bin_id = max(
            int((dispatch_max or {})["max_bin_id"] if dispatch_max is not None else 0),
            int((image_max or {})["max_bin_id"] if image_max is not None else 0),
        )
    with state_lock:
        in_memory_max = max((int(item.get("id") or 0) for item in bin_state["bins"]), default=0)
        return max(max_bin_id, in_memory_max) + 1


def normalize_duplicate_dispatch_request_ids(connection):
    rows = connection.execute(
        """
        SELECT entry_id, request_id, open_request_id
        FROM dispatch_entries
        WHERE COALESCE(TRIM(request_id), '') != ''
        ORDER BY entry_id ASC
        """
    ).fetchall()
    seen = set()
    for row in rows:
        request_id = str(row["request_id"]).strip()
        normalized = slugify_request_id(request_id)
        final_request_id = normalized
        if normalized in seen:
            final_request_id = f"{normalized}-E{int(row['entry_id'])}"
        seen.add(final_request_id)
        if final_request_id != request_id:
            new_open_request_id = row["open_request_id"]
            if str(row["open_request_id"] or "").strip() == request_id:
                new_open_request_id = final_request_id
            connection.execute(
                "UPDATE dispatch_entries SET request_id = ?, open_request_id = ? WHERE entry_id = ?",
                (final_request_id, new_open_request_id, int(row["entry_id"])),
            )
            connection.execute(
                "UPDATE truck_dispatch_items SET request_id = ? WHERE dispatch_entry_id = ?",
                (final_request_id, int(row["entry_id"])),
            )


def get_dispatch_db_connection():
    ensure_database()
    ensure_inspection_database()
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_dispatch_history_database():
    with get_dispatch_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dispatch_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                drone_id TEXT NOT NULL,
                zone_json TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT NOT NULL,
                dispatch_message TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dispatch_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                bin_id INTEGER NOT NULL,
                drone_id TEXT NOT NULL,
                bin_type TEXT NOT NULL,
                label TEXT NOT NULL,
                condition TEXT NOT NULL,
                priority TEXT NOT NULL,
                priority_score INTEGER NOT NULL,
                request_id TEXT,
                open_request_id TEXT,
                has_open_request INTEGER NOT NULL DEFAULT 0,
                duplicate_open_request INTEGER NOT NULL DEFAULT 0,
                location_importance TEXT,
                inspection_confidence REAL NOT NULL DEFAULT 0.9,
                loose_garbage_detected INTEGER NOT NULL DEFAULT 0,
                request_created_at TEXT,
                last_pickup_at TEXT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                address TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                image_id INTEGER,
                review_status TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES dispatch_runs(run_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS truck_dispatch_batches (
                batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                truck_number TEXT NOT NULL,
                route_number TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS truck_dispatch_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                dispatch_entry_id INTEGER NOT NULL,
                bin_id INTEGER NOT NULL,
                request_id TEXT,
                label TEXT NOT NULL,
                api_image_id INTEGER,
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                api_confirmation_status TEXT NOT NULL DEFAULT 'PENDING',
                assigned_at TEXT NOT NULL,
                en_route_at TEXT,
                arrived_at TEXT,
                picked_up_at TEXT,
                api_confirmed_at TEXT,
                closed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES truck_dispatch_batches(batch_id) ON DELETE CASCADE,
                FOREIGN KEY(dispatch_entry_id) REFERENCES dispatch_entries(entry_id) ON DELETE CASCADE,
                FOREIGN KEY(api_image_id) REFERENCES inspection_images(image_id)
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(truck_dispatch_items)").fetchall()
        }
        if "api_image_id" not in existing_columns:
            connection.execute("ALTER TABLE truck_dispatch_items ADD COLUMN api_image_id INTEGER")
        normalize_duplicate_dispatch_request_ids(connection)
        connection.commit()


def record_dispatch_run(summary, mission_id, started_at=None, drone_id=DRONE_ID):
    ensure_dispatch_history_database()
    completed_at = summary.get("captured_at") or utc_now_iso()
    with get_dispatch_db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO dispatch_runs (mission_id, drone_id, zone_json, started_at, completed_at, dispatch_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(mission_id),
                str(drone_id),
                json.dumps(summary.get("zone") or {}),
                str(started_at or ""),
                completed_at,
                str(summary.get("dispatch_message") or ""),
            ),
        )
        run_id = int(cursor.lastrowid)
        for item in summary.get("addresses") or []:
            latest_image = item.get("latest_image") or {}
            connection.execute(
                """
                INSERT INTO dispatch_entries (
                    run_id, bin_id, drone_id, bin_type, label, condition, priority, priority_score,
                    request_id, open_request_id, has_open_request, duplicate_open_request,
                    location_importance, inspection_confidence, loose_garbage_detected,
                    request_created_at, last_pickup_at, lat, lon, address, observed_at, image_id, review_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(item.get("id") or 0),
                    str(item.get("drone_id") or drone_id),
                    str(item.get("bin_type") or ""),
                    str(item.get("label") or ""),
                    str(item.get("condition") or "standard"),
                    str(item.get("priority") or "NORMAL"),
                    int(item.get("priority_score") or 0),
                    str(item.get("request_id") or ""),
                    item.get("open_request_id"),
                    1 if item.get("has_open_request") else 0,
                    1 if item.get("duplicate_open_request") else 0,
                    str(item.get("location_importance") or ""),
                    float(item.get("inspection_confidence") or 0.9),
                    1 if item.get("loose_garbage_detected") else 0,
                    str(item.get("request_created_at") or ""),
                    str(item.get("last_pickup_at") or ""),
                    float(item.get("lat") or 0.0),
                    float(item.get("lon") or 0.0),
                    str(item.get("address") or "Address pending"),
                    str(item.get("captured_at") or completed_at),
                    int(latest_image.get("image_id")) if latest_image.get("image_id") else None,
                    str(latest_image.get("review_status") or ""),
                ),
            )
        connection.commit()
    return run_id


def list_dispatch_history():
    ensure_dispatch_history_database()
    with get_dispatch_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                entry_id, run_id, bin_id, drone_id, bin_type, label, condition, priority, priority_score,
                request_id, open_request_id, has_open_request, duplicate_open_request,
                location_importance, inspection_confidence, loose_garbage_detected,
                request_created_at, last_pickup_at, lat, lon, address, observed_at, image_id, review_status
            FROM dispatch_entries
            ORDER BY priority_score DESC, observed_at ASC, entry_id ASC
            """
        ).fetchall()
    history = []
    counts = {"green": 0, "blue": 0, "garbage": 0}
    for row in rows:
        item = {
            "entry_id": int(row["entry_id"]),
            "run_id": int(row["run_id"]),
            "id": int(row["bin_id"]),
            "drone_id": row["drone_id"],
            "bin_type": row["bin_type"],
            "label": row["label"],
            "condition": row["condition"],
            "priority": row["priority"],
            "priority_score": int(row["priority_score"]),
            "request_id": row["request_id"] or "",
            "open_request_id": row["open_request_id"],
            "has_open_request": bool(row["has_open_request"]),
            "duplicate_open_request": bool(row["duplicate_open_request"]),
            "location_importance": row["location_importance"] or "",
            "inspection_confidence": float(row["inspection_confidence"]),
            "loose_garbage_detected": bool(row["loose_garbage_detected"]),
            "request_created_at": row["request_created_at"] or "",
            "last_pickup_at": row["last_pickup_at"] or "",
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "address": row["address"] or "Address pending",
            "captured_at": row["observed_at"],
            "latest_image": get_image_record(int(row["image_id"]), db_path=DATABASE_PATH) if row["image_id"] else None,
            "review_status": row["review_status"] or "",
        }
        item["truck_assignment"] = get_active_truck_assignment(item["entry_id"])
        counts[item["bin_type"]] = counts.get(item["bin_type"], 0) + 1
        history.append(item)
    return {"entries": history, "counts": counts, "total": len(history)}


def delete_dispatch_entry(entry_id):
    ensure_dispatch_history_database()
    cleanup_image_ids = []
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            "SELECT entry_id, label, image_id FROM dispatch_entries WHERE entry_id = ?",
            (int(entry_id),),
        ).fetchone()
        if not row:
            return None
        truck_image_rows = connection.execute(
            "SELECT api_image_id FROM truck_dispatch_items WHERE dispatch_entry_id = ? AND api_image_id IS NOT NULL",
            (int(entry_id),),
        ).fetchall()
        if row["image_id"]:
            cleanup_image_ids.append(int(row["image_id"]))
        cleanup_image_ids.extend(int(item["api_image_id"]) for item in truck_image_rows if item["api_image_id"])
        connection.execute("DELETE FROM dispatch_entries WHERE entry_id = ?", (int(entry_id),))
        connection.execute(
            """
            DELETE FROM dispatch_runs
            WHERE run_id IN (
                SELECT run_id FROM dispatch_runs
                WHERE run_id NOT IN (SELECT DISTINCT run_id FROM dispatch_entries)
            )
            """
        )
        connection.commit()
    cleanup_unreferenced_images(cleanup_image_ids)
    return {"entry_id": int(row["entry_id"]), "label": row["label"]}


def refresh_dispatch_entry_image(bin_id, entry_id=None, image_id=None, review_status=""):
    ensure_dispatch_history_database()
    latest_image = (
        get_image_record(int(image_id), db_path=DATABASE_PATH)
        if image_id
        else get_latest_image_for_bin(int(bin_id), db_path=DATABASE_PATH)
    )
    latest_image_id = int(latest_image["image_id"]) if latest_image and latest_image.get("image_id") else None
    latest_review_status = str((latest_image or {}).get("review_status") or review_status or "")
    with get_dispatch_db_connection() as connection:
        if entry_id is not None:
            connection.execute(
                """
                UPDATE dispatch_entries
                SET image_id = ?, review_status = ?
                WHERE entry_id = ? AND bin_id = ?
                """,
                (latest_image_id, latest_review_status, int(entry_id), int(bin_id)),
            )
        else:
            connection.execute(
                """
                UPDATE dispatch_entries
                SET image_id = ?, review_status = ?
                WHERE bin_id = ?
                """,
                (latest_image_id, latest_review_status, int(bin_id)),
            )
        connection.commit()
    return latest_image


def cleanup_unreferenced_images(image_ids):
    candidates = sorted({int(image_id) for image_id in image_ids if image_id})
    if not candidates:
        return []
    deleted = []
    with get_dispatch_db_connection() as connection:
        for image_id in candidates:
            dispatch_refs = connection.execute(
                "SELECT COUNT(*) AS count FROM dispatch_entries WHERE image_id = ?",
                (int(image_id),),
            ).fetchone()
            truck_refs = connection.execute(
                "SELECT COUNT(*) AS count FROM truck_dispatch_items WHERE api_image_id = ?",
                (int(image_id),),
            ).fetchone()
            if int(dispatch_refs["count"]) == 0 and int(truck_refs["count"]) == 0:
                if delete_image(int(image_id), db_path=DATABASE_PATH):
                    deleted.append(int(image_id))
    return deleted


def get_truck_progress_steps(item):
    statuses = ["ASSIGNED", "EN_ROUTE", "ARRIVED", "PICKED_UP", "API_PENDING_CONFIRMATION", "CLOSED"]
    current = str(item.get("status") or "ASSIGNED").upper()
    steps = []
    for status in statuses:
        state = "pending"
        if statuses.index(status) < statuses.index(current):
            state = "completed"
        elif status == current:
            state = "active"
        steps.append({"status": status, "state": state})
    return steps


def list_truck_dispatch():
    ensure_dispatch_history_database()
    with get_dispatch_db_connection() as connection:
        batch_rows = connection.execute(
            """
            SELECT batch_id, truck_number, route_number, status, created_at, updated_at
            FROM truck_dispatch_batches
            ORDER BY created_at DESC, batch_id DESC
            """
        ).fetchall()
        item_rows = connection.execute(
            """
            SELECT i.item_id, i.batch_id, i.dispatch_entry_id, i.bin_id, i.request_id, i.label, i.status,
                   api_image_id, api_confirmation_status, assigned_at, en_route_at, arrived_at,
                   picked_up_at, api_confirmed_at, closed_at, updated_at,
                   d.run_id, d.address, d.lat, d.lon, d.image_id, d.review_status, d.condition,
                   d.priority, d.priority_score, r.mission_id
            FROM truck_dispatch_items i
            JOIN dispatch_entries d ON d.entry_id = i.dispatch_entry_id
            JOIN dispatch_runs r ON r.run_id = d.run_id
            ORDER BY assigned_at ASC, item_id ASC
            """
        ).fetchall()
    items_by_batch = {}
    open_count = 0
    for row in item_rows:
        item = {
            "item_id": int(row["item_id"]),
            "batch_id": int(row["batch_id"]),
            "dispatch_entry_id": int(row["dispatch_entry_id"]),
            "bin_id": int(row["bin_id"]),
            "request_id": row["request_id"] or "",
            "flight_run_id": int(row["run_id"]),
            "flight_mission_id": int(row["mission_id"]),
            "address": row["address"] or "Address pending",
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "label": row["label"],
            "status": row["status"],
            "condition": row["condition"] or "standard",
            "priority_level": row["priority"] or "NORMAL",
            "priority_score": int(row["priority_score"] or 0),
            "survey_image": get_image_record(int(row["image_id"]), db_path=DATABASE_PATH) if row["image_id"] else None,
            "survey_review_status": row["review_status"] or "",
            "api_image": get_image_record(int(row["api_image_id"]), db_path=DATABASE_PATH) if row["api_image_id"] else None,
            "api_confirmation_status": row["api_confirmation_status"],
            "assigned_at": row["assigned_at"],
            "en_route_at": row["en_route_at"] or "",
            "arrived_at": row["arrived_at"] or "",
            "picked_up_at": row["picked_up_at"] or "",
            "api_confirmed_at": row["api_confirmed_at"] or "",
            "closed_at": row["closed_at"] or "",
            "updated_at": row["updated_at"],
        }
        item["workflow_steps"] = get_truck_progress_steps(item)
        if item["status"] != "CLOSED":
            open_count += 1
        items_by_batch.setdefault(item["batch_id"], []).append(item)
    batches = []
    for row in batch_rows:
        batch_id = int(row["batch_id"])
        batch_items = items_by_batch.get(batch_id, [])
        batches.append(
            {
                "batch_id": batch_id,
                "truck_number": row["truck_number"],
                "route_number": row["route_number"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "items": batch_items,
                "open_item_count": sum(1 for item in batch_items if item["status"] != "CLOSED"),
            }
        )
    return {"batches": batches, "open_item_count": open_count}


def get_active_truck_assignment(dispatch_entry_id):
    ensure_dispatch_history_database()
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            """
            SELECT i.item_id, i.batch_id, i.status, i.api_confirmation_status,
                   i.assigned_at, i.closed_at, b.truck_number, b.route_number
            FROM truck_dispatch_items i
            JOIN truck_dispatch_batches b ON b.batch_id = i.batch_id
            WHERE i.dispatch_entry_id = ?
            ORDER BY i.item_id DESC
            LIMIT 1
            """,
            (int(dispatch_entry_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "item_id": int(row["item_id"]),
        "batch_id": int(row["batch_id"]),
        "status": row["status"],
        "api_confirmation_status": row["api_confirmation_status"],
        "assigned_at": row["assigned_at"],
        "closed_at": row["closed_at"] or "",
        "truck_number": row["truck_number"],
        "route_number": row["route_number"],
        "is_closed": row["status"] == "CLOSED",
    }


def assign_dispatch_entries_to_truck(entry_ids, truck_number, route_number):
    ensure_dispatch_history_database()
    now = utc_now_iso()
    clean_ids = [int(entry_id) for entry_id in entry_ids]
    assigned = []
    skipped = []
    with get_dispatch_db_connection() as connection:
        batch_cursor = connection.execute(
            """
            INSERT INTO truck_dispatch_batches (truck_number, route_number, status, created_at, updated_at)
            VALUES (?, ?, 'ASSIGNED', ?, ?)
            """,
            (str(truck_number), str(route_number), now, now),
        )
        batch_id = int(batch_cursor.lastrowid)
        for entry_id in clean_ids:
            existing = connection.execute(
                """
                SELECT item_id, status
                FROM truck_dispatch_items
                WHERE dispatch_entry_id = ? AND status != 'CLOSED'
                ORDER BY item_id DESC
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
            if existing:
                skipped.append({"entry_id": entry_id, "reason": f"Already assigned with status {existing['status']}."})
                continue
            entry = connection.execute(
                """
                SELECT entry_id, bin_id, request_id, label
                FROM dispatch_entries
                WHERE entry_id = ?
                """,
                (entry_id,),
            ).fetchone()
            if not entry:
                skipped.append({"entry_id": entry_id, "reason": "Dispatch entry not found."})
                continue
            connection.execute(
                """
                INSERT INTO truck_dispatch_items (
                    batch_id, dispatch_entry_id, bin_id, request_id, label, api_image_id, status,
                    api_confirmation_status, assigned_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, 'ASSIGNED', 'PENDING', ?, ?)
                """,
                (
                    batch_id,
                    int(entry["entry_id"]),
                    int(entry["bin_id"]),
                    entry["request_id"] or "",
                    entry["label"],
                    now,
                    now,
                ),
            )
            assigned.append(int(entry["entry_id"]))
        if not assigned:
            connection.execute("DELETE FROM truck_dispatch_batches WHERE batch_id = ?", (batch_id,))
        connection.commit()
    return {
        "batch_id": batch_id if assigned else None,
        "assigned_entry_ids": assigned,
        "skipped": skipped,
        "truck_dispatch": list_truck_dispatch(),
    }


def advance_truck_dispatch_item(item_id):
    ensure_dispatch_history_database()
    transitions = {
        "ASSIGNED": ("EN_ROUTE", "en_route_at"),
        "EN_ROUTE": ("ARRIVED", "arrived_at"),
        "ARRIVED": ("PICKED_UP", "picked_up_at"),
        "PICKED_UP": ("API_PENDING_CONFIRMATION", None),
        "API_PENDING_CONFIRMATION": ("API_PENDING_CONFIRMATION", None),
        "CLOSED": ("CLOSED", None),
    }
    now = utc_now_iso()
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            "SELECT item_id, batch_id, status FROM truck_dispatch_items WHERE item_id = ?",
            (int(item_id),),
        ).fetchone()
        if not row:
            return None
        current_status = row["status"]
        next_status, timestamp_field = transitions.get(current_status, ("ASSIGNED", None))
        updates = ["status = ?", "updated_at = ?"]
        params = [next_status, now]
        if timestamp_field:
            updates.append(f"{timestamp_field} = ?")
            params.append(now)
        if next_status == "API_PENDING_CONFIRMATION":
            updates.append("api_confirmation_status = ?")
            params.append("PENDING")
        params.append(int(item_id))
        connection.execute(
            f"UPDATE truck_dispatch_items SET {', '.join(updates)} WHERE item_id = ?",
            tuple(params),
        )
        if current_status == "PICKED_UP":
            connection.execute(
                "UPDATE truck_dispatch_batches SET status = ?, updated_at = ? WHERE batch_id = ?",
                ("API_PENDING_CONFIRMATION", now, int(row["batch_id"])),
            )
        else:
            connection.execute(
                "UPDATE truck_dispatch_batches SET status = ?, updated_at = ? WHERE batch_id = ?",
                (next_status, now, int(row["batch_id"])),
                )
        connection.commit()
    return list_truck_dispatch()


def confirm_truck_dispatch_item_api(item_id, confirmation_status="CONFIRMED"):
    ensure_dispatch_history_database()
    now = utc_now_iso()
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            "SELECT item_id, batch_id FROM truck_dispatch_items WHERE item_id = ?",
            (int(item_id),),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            """
            UPDATE truck_dispatch_items
            SET status = 'CLOSED',
                api_confirmation_status = ?,
                api_confirmed_at = ?,
                closed_at = ?,
                updated_at = ?
            WHERE item_id = ?
            """,
            (str(confirmation_status), now, now, now, int(item_id)),
        )
        open_items = connection.execute(
            "SELECT COUNT(*) AS count FROM truck_dispatch_items WHERE batch_id = ? AND status != 'CLOSED'",
            (int(row["batch_id"]),),
        ).fetchone()
        batch_status = "CLOSED" if int(open_items["count"]) == 0 else "API_PENDING_CONFIRMATION"
        connection.execute(
            "UPDATE truck_dispatch_batches SET status = ?, updated_at = ? WHERE batch_id = ?",
            (batch_status, now, int(row["batch_id"])),
        )
        connection.commit()
    return list_truck_dispatch()


def delete_truck_dispatch_item(item_id):
    ensure_dispatch_history_database()
    cleanup_image_ids = []
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            "SELECT item_id, batch_id, label, api_image_id FROM truck_dispatch_items WHERE item_id = ?",
            (int(item_id),),
        ).fetchone()
        if not row:
            return None
        batch_id = int(row["batch_id"])
        if row["api_image_id"]:
            cleanup_image_ids.append(int(row["api_image_id"]))
        connection.execute("DELETE FROM truck_dispatch_items WHERE item_id = ?", (int(item_id),))
        remaining = connection.execute(
            "SELECT COUNT(*) AS count FROM truck_dispatch_items WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if int(remaining["count"]) == 0:
            connection.execute("DELETE FROM truck_dispatch_batches WHERE batch_id = ?", (batch_id,))
        else:
            open_items = connection.execute(
                "SELECT COUNT(*) AS count FROM truck_dispatch_items WHERE batch_id = ? AND status != 'CLOSED'",
                (batch_id,),
            ).fetchone()
            batch_status = "CLOSED" if int(open_items["count"]) == 0 else "ASSIGNED"
            connection.execute(
                "UPDATE truck_dispatch_batches SET status = ?, updated_at = ? WHERE batch_id = ?",
                (batch_status, utc_now_iso(), batch_id),
            )
        connection.commit()
    cleanup_unreferenced_images(cleanup_image_ids)
    return {"item_id": int(row["item_id"]), "batch_id": batch_id, "label": row["label"]}


def build_bin_snapshot_from_dispatch_entry(dispatch_entry_id):
    ensure_dispatch_history_database()
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            """
            SELECT entry_id, bin_id, bin_type, label, condition, request_id, lat, lon, address
            FROM dispatch_entries
            WHERE entry_id = ?
            """,
            (int(dispatch_entry_id),),
        ).fetchone()
    if not row:
        return None
    return {
        "entry_id": int(row["entry_id"]),
        "id": int(row["bin_id"]),
        "bin_type": row["bin_type"],
        "label": row["label"],
        "condition": row["condition"],
        "is_overflowing": str(row["condition"]).lower() == "overflowing",
        "request_id": row["request_id"] or f"BIN-{int(row['bin_id'])}",
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "address": row["address"] or "Address pending",
    }


def store_truck_dispatch_api_image(item_id, file_name, file_bytes, mime_type=None, provider_name="esp32-cam-api"):
    ensure_dispatch_history_database()
    with get_dispatch_db_connection() as connection:
        row = connection.execute(
            """
            SELECT item_id, dispatch_entry_id
            FROM truck_dispatch_items
            WHERE item_id = ?
            """,
            (int(item_id),),
        ).fetchone()
    if not row:
        return None
    bin_snapshot = build_bin_snapshot_from_dispatch_entry(int(row["dispatch_entry_id"]))
    if not bin_snapshot:
        return None
    image = inspection_service.attach_demo_override_image(
        bin_snapshot,
        file_name=file_name,
        file_bytes=file_bytes,
        mime_type=mime_type,
        provider_name=provider_name,
    )
    with get_dispatch_db_connection() as connection:
        connection.execute(
            "UPDATE truck_dispatch_items SET api_image_id = ?, updated_at = ? WHERE item_id = ?",
            (int(image["image_id"]), utc_now_iso(), int(item_id)),
        )
        connection.commit()
    return image


def decode_image_payload(image_base64, file_name="", mime_type=""):
    text = str(image_base64 or "").strip()
    if not text:
        return None
    effective_mime_type = str(mime_type or "").strip()
    payload = text
    if text.startswith("data:") and ";base64," in text:
        header, payload = text.split(",", 1)
        if not effective_mime_type:
            effective_mime_type = header[5:].split(";", 1)[0].strip()
    decoded = b64decode(payload, validate=True)
    effective_file_name = str(file_name or "").strip() or "truck_closure_image"
    effective_mime_type = effective_mime_type or _guess_mime_type(effective_file_name, "image/jpeg")
    return {
        "file_name": effective_file_name,
        "file_bytes": decoded,
        "mime_type": effective_mime_type,
    }


def build_truck_dispatch_payload():
    return {"status": "ok", "truck_dispatch": list_truck_dispatch()}


def enrich_summary_with_history(summary):
    history = list_dispatch_history()
    enriched = dict(summary or {})
    enriched["history_entries"] = history["entries"]
    enriched["history_counts"] = history["counts"]
    enriched["history_total"] = history["total"]
    enriched["truck_dispatch"] = list_truck_dispatch()
    return enriched


def build_history_only_summary():
    history = list_dispatch_history()
    truck_dispatch = list_truck_dispatch()
    if not history["entries"]:
        return {
            "zone": None,
            "total_bins": 0,
            "counts": {"green": 0, "blue": 0, "garbage": 0},
            "addresses": [],
            "history_entries": [],
            "history_counts": {"green": 0, "blue": 0, "garbage": 0},
            "history_total": 0,
            "dispatch_message": "No saved dispatch history is available yet.",
            "survey_complete": False,
            "captured_at": "",
            "priority_formula": (
                "Base priority starts at NORMAL for standard bins and HIGH for overflowing bins, "
                "then adjusts for bin category, time since last pickup, request age, location importance, "
                "existing unresolved request, inspection confidence, and loose garbage detection."
            ),
            "truck_dispatch": truck_dispatch,
        }
    return {
        "zone": None,
        "total_bins": history["total"],
        "counts": history["counts"],
        "addresses": [],
        "history_entries": history["entries"],
        "history_counts": history["counts"],
        "history_total": history["total"],
        "dispatch_message": "Saved dispatch history is available from earlier survey runs.",
        "survey_complete": True,
        "captured_at": history["entries"][0]["captured_at"],
        "truck_dispatch": truck_dispatch,
        "priority_formula": (
            "Base priority starts at NORMAL for standard bins and HIGH for overflowing bins, "
            "then adjusts for bin category, time since last pickup, request age, location importance, "
            "existing unresolved request, inspection confidence, and loose garbage detection."
        ),
    }


def set_nav_state(status=None, message=None):
    with state_lock:
        if status is not None:
            nav_state["status"] = status
        if message is not None:
            nav_state["message"] = message


def next_mission_id():
    with state_lock:
        mission_state["active_id"] += 1
        return mission_state["active_id"]


def get_active_mission_id():
    with state_lock:
        return mission_state["active_id"]


def is_active_mission(mission_id):
    with state_lock:
        return mission_state["active_id"] == mission_id


def reset_progress_state():
    flight_progress["distance_to_goal"] = 0
    flight_progress["progress_percent"] = 0
    flight_progress["target_lat"] = None
    flight_progress["target_lon"] = None
    update_route([])
    update_flight_plan(route_points=[], warnings=[], message="")


def get_nav_status():
    with state_lock:
        return nav_state["status"]


def update_flight_plan(route_points=None, warnings=None, message=None):
    with state_lock:
        if route_points is not None:
            flight_plan_state["route_points"] = [dict(point) for point in route_points]
        if warnings is not None:
            flight_plan_state["warnings"] = list(warnings)
        if message is not None:
            flight_plan_state["message"] = message


def get_flight_plan_snapshot():
    with state_lock:
        return {
            "route_points": [dict(point) for point in flight_plan_state["route_points"]],
            "warnings": list(flight_plan_state["warnings"]),
            "message": flight_plan_state["message"],
        }


def get_bins_snapshot():
    with state_lock:
        return [dict(item) for item in bin_state["bins"]]


def get_dispatch_snapshot():
    history_summary = build_history_only_summary()
    with state_lock:
        current_summary = dict(bin_state["last_summary"]) if bin_state["last_summary"] else None
        return {
            "bins": [dict(item) for item in bin_state["bins"]],
            "last_zone": dict(bin_state["last_zone"]) if bin_state["last_zone"] else None,
            "last_summary": enrich_summary_with_history(current_summary) if current_summary else history_summary,
            "mission": dict(zone_mission_state),
        }


def find_bin_by_id(bin_id):
    for item in bin_state["bins"]:
        if item["id"] == bin_id:
            return item
    return None


def update_bin_condition_fields(bin_item, used_request_ids=None):
    is_overflowing = bool(bin_item.get("is_overflowing", False))
    bin_item["condition"] = "overflowing" if is_overflowing else "standard"
    bin_item.setdefault("last_pickup_at", "")
    bin_item.setdefault("request_created_at", utc_now_iso())
    bin_item.setdefault("location_importance", "")
    bin_item.setdefault("inspection_confidence", 0.9)
    bin_item.setdefault("loose_garbage_detected", False)
    bin_item.setdefault("has_open_request", False)
    if used_request_ids is None:
        bin_item["request_id"] = ensure_unique_request_id(
            bin_item.get("request_id"),
            bin_id=bin_item.get("id"),
            exclude_bin_id=bin_item.get("id"),
        )
    else:
        bin_item["request_id"] = ensure_unique_request_id_from_used(
            used_request_ids,
            base_request_id=bin_item.get("request_id"),
            bin_id=bin_item.get("id"),
        )
    bin_item.setdefault("open_request_id", None)
    bin_item.update(calculate_priority(bin_item))
    return bin_item


def point_in_zone(bin_item, zone):
    return zone["south"] <= bin_item["lat"] <= zone["north"] and zone["west"] <= bin_item["lon"] <= zone["east"]


def zone_center(zone):
    return {
        "lat": (zone["south"] + zone["north"]) / 2.0,
        "lon": (zone["west"] + zone["east"]) / 2.0,
    }


def build_pending_zone_summary(zone, message=None):
    return {
        "zone": zone,
        "total_bins": 0,
        "counts": {"green": 0, "blue": 0, "garbage": 0},
        "addresses": [],
        "dispatch_message": message or "Zone selected. Fly the drone across the full zone to collect dispatch details.",
        "survey_complete": False,
        "captured_at": "",
        "drone_id": DRONE_ID,
        "priority_formula": (
            "Base priority starts at NORMAL for standard bins and HIGH for overflowing bins, "
            "then adjusts for bin category, time since last pickup, request age, location importance, "
            "existing unresolved request, inspection confidence, and loose garbage detection."
        ),
    }


def store_zone_summary(summary):
    with state_lock:
        bin_state["last_zone"] = dict(summary["zone"])
        bin_state["last_summary"] = dict(summary)


def set_zone_mission_state(status=None, message=None, completed_waypoints=None, total_waypoints=None, started_at=None, drone_id=None):
    with state_lock:
        if status is not None:
            zone_mission_state["status"] = status
        if message is not None:
            zone_mission_state["message"] = message
        if completed_waypoints is not None:
            zone_mission_state["completed_waypoints"] = completed_waypoints
        if total_waypoints is not None:
            zone_mission_state["total_waypoints"] = total_waypoints
        if started_at is not None:
            zone_mission_state["started_at"] = started_at
        if drone_id is not None:
            zone_mission_state["drone_id"] = drone_id


def summarize_zone(zone, survey_complete=True):
    bins_in_zone = [item for item in get_bins_snapshot() if point_in_zone(item, zone)]
    prioritized_bins = prioritize_dispatch_items(bins_in_zone)
    prioritized_bins = attach_latest_images(prioritized_bins)
    counts = {"green": 0, "blue": 0, "garbage": 0}
    addresses = []
    captured_at = utc_now_iso()

    for item in prioritized_bins:
        latest_image = item.get("latest_image")
        observed_at = (latest_image or {}).get("captured_at") or captured_at
        counts[item["bin_type"]] = counts.get(item["bin_type"], 0) + 1
        addresses.append(
            {
                "id": item["id"],
                "bin_type": item["bin_type"],
                "label": item.get("label") or item["bin_type"].title(),
                "condition": item.get("condition", "standard"),
                "priority": item.get("priority_level", "NORMAL"),
                "priority_score": item.get("priority_score", 30),
                "request_id": item.get("request_id"),
                "open_request_id": item.get("open_request_id"),
                "has_open_request": bool(item.get("has_open_request", False)),
                "duplicate_open_request": bool(item.get("duplicate_open_request", False)),
                "location_importance": item.get("location_importance", "residential"),
                "inspection_confidence": item.get("inspection_confidence", 0.9),
                "loose_garbage_detected": bool(item.get("loose_garbage_detected", False)),
                "priority_factors": item.get("priority_factors", {}),
                "request_created_at": item.get("request_created_at", ""),
                "last_pickup_at": item.get("last_pickup_at", ""),
                "lat": item["lat"],
                "lon": item["lon"],
                "address": item.get("address") or "Address pending",
                "latest_image": latest_image,
                "captured_at": observed_at,
                "drone_id": DRONE_ID,
            }
        )

    summary = {
        "zone": zone,
        "total_bins": len(prioritized_bins),
        "counts": counts,
        "addresses": addresses,
        "dispatch_message": (
            f"Dispatch summary ready for {len(prioritized_bins)} bin(s) in the selected zone."
            if prioritized_bins
            else "No waste bins found inside the selected zone."
        ),
        "survey_complete": survey_complete,
        "captured_at": captured_at,
        "drone_id": DRONE_ID,
        "priority_formula": (
            "Base priority starts at NORMAL for standard bins and HIGH for overflowing bins, "
            "then adjusts for bin category, time since last pickup, request age, location importance, "
            "existing unresolved request, inspection confidence, and loose garbage detection."
        ),
    }
    store_zone_summary(summary)
    return summary


def build_inspection_context(zone, waypoint, mission_id):
    return {
        "zone": dict(zone),
        "waypoint": {"lat": float(waypoint["lat"]), "lon": float(waypoint["lon"])},
        "mission_id": int(mission_id),
        "captured_at": utc_now_iso(),
    }


def capture_zone_bin_evidence(zone, waypoint, mission_id, captured_bin_ids):
    bins_in_zone = [item for item in get_bins_snapshot() if point_in_zone(item, zone)]
    for bin_item in bins_in_zone:
        if int(bin_item["id"]) in captured_bin_ids:
            continue
        if not inspection_service.capture_needed_for_bin(bin_item, waypoint):
            continue
        inspection_service.capture_bin_evidence(bin_item, build_inspection_context(zone, waypoint, mission_id))
        captured_bin_ids.add(int(bin_item["id"]))


def set_feedback_stage(stage, **updates):
    with state_lock:
        feedback_state["stage"] = stage
        for key, value in updates.items():
            feedback_state[key] = value


def get_weather_snapshot():
    with state_lock:
        return dict(weather_state)


def get_demo_snapshot():
    with state_lock:
        return dict(demo_state)


def map_drone_status(status):
    mapping = {
        "idle": "IDLE",
        "flying": "EN_ROUTE",
        "rerouting": "EN_ROUTE",
        "surveying": "INSPECTING",
        "arrived": "IDLE",
        "taking_off": "EN_ROUTE",
        "error": "ERROR",
    }
    return mapping.get(str(status or "").lower(), "IDLE")


def get_current_assignment_id():
    status = get_nav_status()
    if status == "surveying":
        return "ZONE-SURVEY"
    target_lat = flight_progress.get("target_lat")
    target_lon = flight_progress.get("target_lon")
    if status in {"flying", "rerouting"} and target_lat is not None and target_lon is not None:
        return f"NAV-{target_lat:.4f}-{target_lon:.4f}"
    if status == "taking_off":
        return "TAKEOFF"
    return None


def get_battery_level():
    try:
        state = get_multirotor_state()
    except Exception:
        return None

    for attr in ("battery_remaining", "battery_level"):
        value = getattr(state, attr, None)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 1.0:
            return round(value * 100, 1)
        if 0.0 <= value <= 100.0:
            return round(value, 1)
    return None


def build_drone_snapshot():
    with state_lock:
        raw_status = nav_state["status"]
        message = nav_state["message"]
    return {
        "id": DRONE_ID,
        "status": raw_status,
        "status_label": map_drone_status(raw_status),
        "assignment_id": get_current_assignment_id(),
        "battery_level": get_battery_level(),
        "message": message,
    }


def apply_weather_state(rain, wind, snow):
    applied = set_sim_weather(rain, wind, snow)
    with state_lock:
        weather_state.update(applied)
    return applied


def apply_speed_state(multiplier):
    applied = set_speed_multiplier(multiplier)
    with state_lock:
        demo_state["speed_multiplier"] = applied["multiplier"]
    return applied


def update_route(points):
    with state_lock:
        feedback_state["active_route"] = [dict(point) for point in points]


def current_route_start():
    try:
        position = get_position()
        return {"lat": position["lat"], "lon": position["lon"]}
    except Exception:
        return dict(HOME_POINT)


def validate_zone_survey_plan(zone):
    current_point = current_route_start()
    survey_waypoints = generate_zone_scan_waypoints(zone)
    entry_point = survey_waypoints[0] if survey_waypoints else zone_center(zone)
    first_leg = prepare_route_validation(current_point, entry_point)
    if not first_leg["allowed"]:
        return first_leg
    if not survey_waypoints:
        return first_leg

    cursor = current_point
    collected_route = [dict(current_point)]
    collected_warnings = list(first_leg.get("warnings", []))
    rerouted = bool(first_leg.get("rerouted"))

    for waypoint in survey_waypoints:
        leg = validate_route_plan(cursor, waypoint, return_destination=HOME_POINT)
        if not leg["allowed"]:
            return {
                "allowed": False,
                "error": "Dispatch blocked: the selected zone overlaps restricted airspace and cannot be surveyed safely.",
                "details": leg["error"],
                "route_points": collected_route,
                "warnings": collected_warnings,
            }
        rerouted = rerouted or bool(leg.get("rerouted"))
        collected_warnings.extend(leg.get("warnings", []))
        for point in leg["route_points"][1:]:
            collected_route.append(dict(point))
        cursor = {"lat": waypoint["lat"], "lon": waypoint["lon"]}

    return {
        "allowed": True,
        "route_points": first_leg["route_points"],
        "warnings": list(dict.fromkeys(collected_warnings)),
        "rerouted": rerouted,
        "message": first_leg.get("message"),
    }


def generate_zone_scan_waypoints(zone):
    south = zone["south"]
    north = zone["north"]
    west = zone["west"]
    east = zone["east"]
    lat_span = max(0.0, north - south)
    lon_span = max(0.0, east - west)
    if lat_span == 0.0 and lon_span == 0.0:
        return [zone_center(zone)]

    horizontal_scan = lon_span >= lat_span
    lane_spacing = 0.00012
    waypoints = []
    if horizontal_scan:
        lane_count = max(2, int(math.ceil(lat_span / lane_spacing)) + 1)
        for index in range(lane_count):
            ratio = index / (lane_count - 1) if lane_count > 1 else 0.5
            lat = south + lat_span * ratio
            if index % 2 == 0:
                waypoints.append({"lat": lat, "lon": west})
                waypoints.append({"lat": lat, "lon": east})
            else:
                waypoints.append({"lat": lat, "lon": east})
                waypoints.append({"lat": lat, "lon": west})
    else:
        lane_count = max(2, int(math.ceil(lon_span / lane_spacing)) + 1)
        for index in range(lane_count):
            ratio = index / (lane_count - 1) if lane_count > 1 else 0.5
            lon = west + lon_span * ratio
            if index % 2 == 0:
                waypoints.append({"lat": south, "lon": lon})
                waypoints.append({"lat": north, "lon": lon})
            else:
                waypoints.append({"lat": north, "lon": lon})
                waypoints.append({"lat": south, "lon": lon})
    return waypoints


def build_weather_reroute(current_pos, destination, weather):
    dx = destination["lon"] - current_pos["lon"]
    dy = destination["lat"] - current_pos["lat"]
    length = math.hypot(dx, dy)
    if length == 0:
        return {"lat": destination["lat"], "lon": destination["lon"]}

    severity = max(weather["rain"], weather["wind"], weather["snow"])
    offset_scale = 0.0025 * (severity / 100.0)
    perp_x = -dy / length
    perp_y = dx / length
    midpoint_lat = current_pos["lat"] + (destination["lat"] - current_pos["lat"]) * 0.55
    midpoint_lon = current_pos["lon"] + (destination["lon"] - current_pos["lon"]) * 0.55
    return {
        "lat": midpoint_lat + perp_y * offset_scale,
        "lon": midpoint_lon + perp_x * offset_scale,
    }


def weather_feedback_hook(context):
    with state_lock:
        feedback_state["cycle"] += 1
        cycle = feedback_state["cycle"]

    set_feedback_stage("sense", reason="Reading user-set weather and current drone position.")
    weather = get_weather_snapshot()
    current_position = context["position"]
    destination = context["destination"]
    time.sleep(0.15)

    set_feedback_stage("analyze", weather=weather, reason="Checking whether rain, wind, or snow exceeds 50%.")
    unsafe = any(value > 50 for value in weather.values())
    time.sleep(0.15)

    if unsafe:
        reroute = build_weather_reroute(current_position, destination, weather)
        route_points = [
            {"lat": current_position["lat"], "lon": current_position["lon"]},
            reroute,
            {"lat": destination["lat"], "lon": destination["lon"]},
        ]
        update_route(route_points)
        with state_lock:
            feedback_state["reroute_count"] += 1
            reroute_count = feedback_state["reroute_count"]
        set_feedback_stage(
            "act",
            safe=False,
            weather=weather,
            last_reroute=reroute,
            reroute_count=reroute_count,
            reason="Conditions are not safe for flight",
        )
        result = {"safe": False, "message": "Conditions are not safe for flight", "reroute": reroute}
    else:
        route_points = [
            {"lat": current_position["lat"], "lon": current_position["lon"]},
            {"lat": destination["lat"], "lon": destination["lon"]},
        ]
        update_route(route_points)
        set_feedback_stage(
            "act",
            safe=True,
            weather=weather,
            reason="Weather is within safe limits. Continue on current path.",
        )
        result = {"safe": True, "message": "Weather safe. Continue on current path.", "clear_weather_route": True}

    time.sleep(0.15)
    set_feedback_stage(
        "repeat",
        weather=weather,
        reason=f"Feedback loop cycle {cycle} complete. Waiting for next user/weather update.",
    )
    return result


def prepare_route_validation(start_point, destination_point):
    validation = validate_route_plan(start_point, destination_point, return_destination=HOME_POINT)
    update_flight_plan(
        route_points=validation.get("route_points", [start_point, destination_point]),
        warnings=validation.get("warnings", []),
        message=validation.get("message") or validation.get("error") or "",
    )
    return validation


def revalidate_active_route():
    status = get_nav_status()
    if status not in {"flying", "surveying"}:
        return
    plan = get_flight_plan_snapshot()
    route_points = plan["route_points"]
    if len(route_points) < 2:
        return
    validation = prepare_route_validation(route_points[0], route_points[-1])
    if validation["allowed"]:
        update_route(validation["route_points"])
        if validation.get("message"):
            set_nav_state(status, validation["message"])
        elif validation.get("warnings"):
            set_nav_state(status, validation["warnings"][0])
    else:
        reset_progress_state()
        set_nav_state("error", validation["error"])


def execute_route(route_points, transit_status, completion_message, mission_id):
    if len(route_points) < 2:
        raise ValueError("A route requires at least two points.")

    update_route(route_points)
    update_flight_plan(route_points=route_points)
    with state_lock:
        feedback_state["cycle"] = 0
        feedback_state["stage"] = "sense"
        feedback_state["safe"] = True
        feedback_state["reason"] = "Feedback loop armed for this route."
        feedback_state["last_reroute"] = None
        feedback_state["reroute_count"] = 0
        feedback_state["sample_input"] = {
            "origin": {"lat": route_points[0]["lat"], "lon": route_points[0]["lon"]},
            "destination": {"lat": route_points[-1]["lat"], "lon": route_points[-1]["lon"]},
            "weather": dict(weather_state),
        }

    for index, waypoint in enumerate(route_points[1:], start=1):
        if not is_active_mission(mission_id):
            return
        set_nav_state(
            transit_status,
            f"{transit_status.title()} via waypoint {index}/{len(route_points) - 1} at {waypoint['lat']:.5f}, {waypoint['lon']:.5f}",
        )
        with state_lock:
            feedback_state["sample_input"]["destination"] = {"lat": waypoint["lat"], "lon": waypoint["lon"]}
        fly_to(waypoint["lat"], waypoint["lon"], feedback_hook=weather_feedback_hook)
    if not is_active_mission(mission_id):
        return
    set_nav_state("arrived", completion_message)


def begin_navigation(lat, lon, route_points=None, message_prefix="Navigating to"):
    lat = float(lat)
    lon = float(lon)
    current_origin = get_position()
    base_route = route_points or [
        {"lat": current_origin["lat"], "lon": current_origin["lon"]},
        {"lat": lat, "lon": lon},
    ]
    mission_id = next_mission_id()
    reset_progress_state()
    set_nav_state("flying", f"{message_prefix} {lat:.5f}, {lon:.5f}")

    def nav_thread():
        try:
            execute_route(base_route, "flying", "Destination reached.", mission_id)
        except Exception as exc:
            print(f"Navigation error: {exc}")
            reset_progress_state()
            set_nav_state("error", f"Navigation error: {exc}")

    threading.Thread(target=nav_thread, daemon=True).start()


def begin_zone_survey(zone):
    waypoints = generate_zone_scan_waypoints(zone)
    mission_id = next_mission_id()
    captured_bin_ids = set()
    survey_started_at = utc_now_iso()
    reset_progress_state()
    set_zone_mission_state(
        status="in_progress",
        message=f"Survey mission started with {len(waypoints)} scan waypoint(s).",
        completed_waypoints=0,
        total_waypoints=len(waypoints),
        started_at=survey_started_at,
        drone_id=DRONE_ID,
    )
    store_zone_summary(
        build_pending_zone_summary(
            zone,
            message="Drone is surveying the selected zone. Dispatch summary will appear after full coverage.",
        )
    )
    current_origin = get_position()
    survey_route = [{"lat": current_origin["lat"], "lon": current_origin["lon"]}] + [dict(point) for point in waypoints]
    update_route(survey_route)
    update_flight_plan(route_points=survey_route)
    first_target = waypoints[0] if waypoints else zone_center(zone)
    approach_validation = prepare_route_validation(current_origin, {"lat": first_target["lat"], "lon": first_target["lon"]})
    initial_status = "rerouting" if approach_validation.get("rerouted") else "flying"
    initial_message = approach_validation.get("message") or (
        "Rerouting around restricted airspace to reach the selected zone."
        if approach_validation.get("rerouted")
        else "En route to the selected zone."
    )
    set_nav_state(initial_status, initial_message)

    with state_lock:
        feedback_state["cycle"] = 0
        feedback_state["stage"] = "sense"
        feedback_state["safe"] = True
        feedback_state["reason"] = "Feedback loop armed for this zone survey."
        feedback_state["last_reroute"] = None
        feedback_state["reroute_count"] = 0
        feedback_state["sample_input"] = {
            "origin": {"lat": current_origin["lat"], "lon": current_origin["lon"]},
            "destination": {"lat": first_target["lat"], "lon": first_target["lon"]},
            "weather": dict(weather_state),
        }

    def survey_thread():
        try:
            total_waypoints = len(waypoints)
            for index, point in enumerate(waypoints, start=1):
                if not is_active_mission(mission_id):
                    return
                position = get_position()
                start_point = {"lat": position["lat"], "lon": position["lon"]}
                validation = prepare_route_validation(start_point, {"lat": point["lat"], "lon": point["lon"]})
                if not validation["allowed"]:
                    raise RuntimeError(validation["error"])
                route_points = validation["route_points"]
                if index == 1:
                    transit_status = "rerouting" if validation.get("rerouted") else "flying"
                    transit_message = validation.get("message") or (
                        "Rerouting around restricted airspace to reach the selected zone."
                        if validation.get("rerouted")
                        else f"En route to zone entry waypoint {index}/{total_waypoints}."
                    )
                else:
                    transit_status = "surveying"
                    transit_message = (
                        validation.get("message")
                        if validation.get("rerouted")
                        else f"Surveying zone waypoint {index}/{total_waypoints} at {point['lat']:.5f}, {point['lon']:.5f}"
                    )
                    if validation.get("rerouted") and not transit_message:
                        transit_message = f"Rerouting within zone survey around restricted airspace at waypoint {index}/{total_waypoints}."
                set_nav_state(transit_status, transit_message)
                update_route(route_points + [dict(item) for item in waypoints[index:]])
                with state_lock:
                    feedback_state["sample_input"]["destination"] = {"lat": point["lat"], "lon": point["lon"]}
                for segment_end in route_points[1:]:
                    if not is_active_mission(mission_id):
                        return
                    fly_to(segment_end["lat"], segment_end["lon"], feedback_hook=weather_feedback_hook)
                if index == 1:
                    set_nav_state("surveying", f"Reached the selected zone. Beginning survey coverage at waypoint {index}/{total_waypoints}.")
                capture_zone_bin_evidence(zone, point, mission_id, captured_bin_ids)
                set_zone_mission_state(
                    status="in_progress",
                    message=f"Completed {index} of {total_waypoints} survey waypoint(s).",
                    completed_waypoints=index,
                    total_waypoints=total_waypoints,
                )

            final_summary = summarize_zone(zone, survey_complete=True)
            record_dispatch_run(final_summary, mission_id=mission_id, started_at=survey_started_at, drone_id=DRONE_ID)
            set_zone_mission_state(
                status="completed",
                message="Zone survey complete. Dispatch summary is ready.",
                completed_waypoints=total_waypoints,
                total_waypoints=total_waypoints,
                started_at=survey_started_at,
                drone_id=DRONE_ID,
            )
            if not is_active_mission(mission_id):
                return
            set_nav_state("arrived", final_summary["dispatch_message"])
        except Exception as exc:
            pending_summary = build_pending_zone_summary(
                zone,
                message="Zone survey did not complete. Dispatch summary is still pending.",
            )
            store_zone_summary(pending_summary)
            set_zone_mission_state(status="error", message=f"Zone survey error: {exc}")
            print(f"Zone survey error: {exc}")
            reset_progress_state()
            set_nav_state("error", f"Zone survey error: {exc}")

    threading.Thread(target=survey_thread, daemon=True).start()


ensure_database()
ensure_inspection_database()
ensure_dispatch_history_database()
seeded_next_bin_id = next_bin_id_seed()
with state_lock:
    bin_state["next_id"] = max(int(bin_state.get("next_id") or 1), seeded_next_bin_id)
try:
    apply_weather_state(0, 0, 0)
except Exception:
    pass


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/position")
def position():
    try:
        pos = get_position()
    except Exception as exc:
        set_nav_state("error", f"Position error: {exc}")
        return jsonify(
            {
                "status": nav_state["status"],
                "message": nav_state["message"],
                "distance_to_goal": flight_progress.get("distance_to_goal", 0),
                "progress_percent": flight_progress.get("progress_percent", 0),
                "target_lat": flight_progress.get("target_lat"),
                "target_lon": flight_progress.get("target_lon"),
                "feedback": feedback_state,
                "weather": get_weather_snapshot(),
                "dispatch": get_dispatch_snapshot(),
                "demo": get_demo_snapshot(),
                "no_fly_zones": list_zones(include_inactive=True),
                "flight_plan": get_flight_plan_snapshot(),
            }
        ), 500

    with state_lock:
        pos["status"] = nav_state["status"]
        pos["message"] = nav_state["message"]
        pos["feedback"] = dict(feedback_state)

    pos["distance_to_goal"] = flight_progress.get("distance_to_goal", 0)
    pos["progress_percent"] = flight_progress.get("progress_percent", 0)
    pos["target_lat"] = flight_progress.get("target_lat")
    pos["target_lon"] = flight_progress.get("target_lon")
    pos["weather"] = get_weather_snapshot()
    pos["dispatch"] = get_dispatch_snapshot()
    pos["demo"] = get_demo_snapshot()
    pos["drone"] = build_drone_snapshot()
    pos["no_fly_zones"] = list_zones(include_inactive=True)
    pos["flight_plan"] = get_flight_plan_snapshot()
    return jsonify(pos)


@app.route("/no-fly-zones", methods=["GET", "POST"])
def no_fly_zones_collection():
    if request.method == "GET":
        return jsonify({"zones": list_zones(include_inactive=True)})

    data = request.json or {}
    try:
        payload = validate_zone_payload(data)
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    zone = create_zone(**payload)
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones/<int:zone_id>", methods=["PUT", "DELETE"])
def no_fly_zone_item(zone_id):
    existing = get_zone(zone_id)
    if not existing:
        return jsonify({"status": "error", "message": "No-fly zone not found."}), 404

    if request.method == "DELETE":
        delete_zone(zone_id)
        revalidate_active_route()
        return jsonify({"status": "ok"})

    data = request.json or {}
    merged = {
        "name": data.get("name", existing["name"]),
        "description": data.get("description", existing["description"]),
        "shape_type": data.get("shape_type", existing["shape_type"]),
        "geometry": data.get("geometry", existing["geometry"]),
        "buffer_distance_meters": data.get("buffer_distance_meters", existing["buffer_distance_meters"]),
        "is_active": data.get("is_active", existing["is_active"]),
        "created_by": existing["created_by"],
    }
    try:
        payload = validate_zone_payload(merged)
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    zone = update_zone(
        zone_id,
        payload["name"],
        payload["description"],
        payload["shape_type"],
        payload["geometry"],
        payload["buffer_distance_meters"],
        payload["is_active"],
    )
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones/<int:zone_id>/active", methods=["POST"])
def no_fly_zone_active(zone_id):
    data = request.json or {}
    if "is_active" not in data:
        return jsonify({"status": "error", "message": "is_active is required."}), 400
    zone = set_zone_active(zone_id, bool(data["is_active"]))
    if not zone:
        return jsonify({"status": "error", "message": "No-fly zone not found."}), 404
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones", methods=["DELETE"])
def clear_no_fly_zones():
    zones = list_zones(include_inactive=True)
    for zone in zones:
        delete_zone(zone["id"])
    next_mission_id()
    reset_progress_state()
    set_nav_state("idle", "All no-fly zones cleared.")
    return jsonify({"status": "ok", "deleted": len(zones)})


@app.route("/bins", methods=["GET", "POST", "DELETE"])
def bins():
    if request.method == "GET":
        return jsonify(get_dispatch_snapshot())

    if request.method == "DELETE":
        next_id = next_bin_id_seed()
        with state_lock:
            bin_state["bins"] = []
            bin_state["last_zone"] = None
            bin_state["last_summary"] = None
            bin_state["next_id"] = next_id
            zone_mission_state["status"] = "idle"
            zone_mission_state["message"] = "No zone survey has been started."
            zone_mission_state["completed_waypoints"] = 0
            zone_mission_state["total_waypoints"] = 0
            zone_mission_state["started_at"] = ""
            zone_mission_state["drone_id"] = DRONE_ID
        return jsonify({"status": "ok"})

    data = request.json or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Valid bin coordinates are required."}), 400

    bin_type = str(data.get("bin_type", "")).strip().lower()
    if bin_type not in {"green", "blue", "garbage"}:
        return jsonify({"status": "error", "message": "Bin type must be green, blue, or garbage."}), 400

    with state_lock:
        bin_id = bin_state["next_id"]
        bin_state["next_id"] += 1
        used_request_ids = {
            str(item.get("request_id") or "").strip().upper()
            for item in bin_state["bins"]
        }
        bin_item = {
            "id": bin_id,
            "bin_type": bin_type,
            "is_overflowing": bool(data.get("is_overflowing", False)),
            "lat": lat,
            "lon": lon,
            "address": str(data.get("address") or "").strip() or "Address pending",
            "label": str(data.get("label") or "").strip() or f"{bin_type.title()} Bin",
            "last_pickup_at": str(data.get("last_pickup_at") or "").strip(),
            "request_created_at": str(data.get("request_created_at") or "").strip() or utc_now_iso(),
            "location_importance": str(data.get("location_importance") or "").strip().lower(),
            "inspection_confidence": data.get("inspection_confidence", 0.9),
            "loose_garbage_detected": bool(data.get("loose_garbage_detected", False)),
            "has_open_request": bool(data.get("has_open_request", False)),
            "request_id": ensure_unique_request_id_from_used(
                used_request_ids,
                base_request_id=str(data.get("request_id") or f"BIN-{bin_id}").strip(),
                bin_id=bin_id,
            ),
            "open_request_id": str(data.get("open_request_id") or "").strip() or None,
            "simulated_moving_vehicle_attempts": int(data.get("simulated_moving_vehicle_attempts", 1 if bin_id % 5 == 0 else 0)),
        }
        update_bin_condition_fields(bin_item, used_request_ids=used_request_ids)
        bin_state["bins"].append(bin_item)
    return jsonify({"status": "ok", "bin": bin_item})


@app.route("/bins/<int:bin_id>/overflow", methods=["POST"])
def toggle_bin_overflow(bin_id):
    data = request.json or {}
    if "is_overflowing" not in data:
        return jsonify({"status": "error", "message": "Overflow state is required."}), 400

    with state_lock:
        bin_item = find_bin_by_id(bin_id)
        if not bin_item:
            return jsonify({"status": "error", "message": "Bin not found."}), 404
        used_request_ids = {
            str(item.get("request_id") or "").strip().upper()
            for item in bin_state["bins"]
            if int(item.get("id", -1)) != int(bin_id)
        }
        bin_item["is_overflowing"] = bool(data.get("is_overflowing"))
        update_bin_condition_fields(bin_item, used_request_ids=used_request_ids)
        updated_bin = dict(bin_item)

    return jsonify(
        {
            "status": "ok",
            "bin": updated_bin,
            "message": (
                f"{updated_bin['label']} marked as overflowing."
                if updated_bin["is_overflowing"]
                else f"{updated_bin['label']} restored to standard condition."
            ),
        }
    )


@app.route("/bins/<int:bin_id>", methods=["PUT"])
def update_bin(bin_id):
    data = request.json or {}
    with state_lock:
        bin_item = find_bin_by_id(bin_id)
        if not bin_item:
            return jsonify({"status": "error", "message": "Bin not found."}), 404
        used_request_ids = {
            str(item.get("request_id") or "").strip().upper()
            for item in bin_state["bins"]
            if int(item.get("id", -1)) != int(bin_id)
        }
        if "address" in data:
            bin_item["address"] = str(data.get("address") or "").strip() or bin_item["address"]
        if "label" in data:
            bin_item["label"] = str(data.get("label") or "").strip() or bin_item["label"]
        if "last_pickup_at" in data:
            bin_item["last_pickup_at"] = str(data.get("last_pickup_at") or "").strip()
        if "request_created_at" in data:
            bin_item["request_created_at"] = str(data.get("request_created_at") or "").strip() or bin_item["request_created_at"]
        if "location_importance" in data:
            bin_item["location_importance"] = str(data.get("location_importance") or "").strip().lower()
        if "inspection_confidence" in data:
            bin_item["inspection_confidence"] = data.get("inspection_confidence", bin_item.get("inspection_confidence", 0.9))
        if "loose_garbage_detected" in data:
            bin_item["loose_garbage_detected"] = bool(data.get("loose_garbage_detected"))
        if "has_open_request" in data:
            bin_item["has_open_request"] = bool(data.get("has_open_request"))
        if "request_id" in data:
            requested_request_id = str(data.get("request_id") or "").strip() or bin_item["request_id"]
            bin_item["request_id"] = ensure_unique_request_id_from_used(
                used_request_ids,
                base_request_id=requested_request_id,
                bin_id=bin_item.get("id"),
            )
        if "open_request_id" in data:
            bin_item["open_request_id"] = str(data.get("open_request_id") or "").strip() or None
        if "simulated_moving_vehicle_attempts" in data:
            bin_item["simulated_moving_vehicle_attempts"] = int(data.get("simulated_moving_vehicle_attempts") or 0)
        update_bin_condition_fields(bin_item, used_request_ids=used_request_ids)
        updated_bin = dict(bin_item)
    return jsonify({"status": "ok", "bin": updated_bin})


@app.route("/inspection-images/<int:image_id>")
def inspection_image_item(image_id):
    image = get_image_record(image_id, db_path=DATABASE_PATH)
    if not image:
        return jsonify({"status": "error", "message": "Inspection image not found."}), 404
    return jsonify({"status": "ok", "image": image})


@app.route("/inspection-images/<int:image_id>/file")
def inspection_image_file(image_id):
    image = get_image_record(image_id)
    if not image or not image.get("processed_image_path"):
        return jsonify({"status": "error", "message": "Processed inspection image not found."}), 404
    resolved_path = resolve_stored_image_path(image["processed_image_path"], DATABASE_PATH)
    if not resolved_path or not os.path.exists(resolved_path):
        return jsonify({"status": "error", "message": "Processed inspection image file is missing."}), 404
    return send_file(resolved_path, mimetype="image/svg+xml")


@app.route("/inspection-images/<int:image_id>/manual-review", methods=["POST"])
def inspection_image_manual_review(image_id):
    data = request.json or {}
    required = bool(data.get("manual_review_required", True))
    image = set_manual_review(image_id, required, db_path=DATABASE_PATH)
    if not image:
        return jsonify({"status": "error", "message": "Inspection image not found."}), 404
    return jsonify({"status": "ok", "image": image})


@app.route("/bins/<int:bin_id>/demo-image", methods=["POST"])
def bin_demo_image_upload(bin_id):
    with state_lock:
        bin_item = find_bin_by_id(bin_id)
        if not bin_item:
            return jsonify({"status": "error", "message": "Bin not found."}), 404
        bin_snapshot = dict(bin_item)
        last_zone = dict(bin_state["last_zone"]) if bin_state["last_zone"] else None
        survey_complete = bool(bin_state["last_summary"] and bin_state["last_summary"].get("survey_complete"))

    uploaded = request.files.get("image")
    if not uploaded or not uploaded.filename:
        return jsonify({"status": "error", "message": "A demo image file is required."}), 400

    file_bytes = uploaded.read()
    if not file_bytes:
        return jsonify({"status": "error", "message": "Uploaded image is empty."}), 400

    mime_type = uploaded.mimetype or _guess_mime_type(uploaded.filename, "image/jpeg")
    if not mime_type.startswith("image/"):
        return jsonify({"status": "error", "message": "Only image uploads are supported."}), 400

    entry_id_raw = request.form.get("entry_id")
    try:
        entry_id = int(entry_id_raw) if str(entry_id_raw or "").strip() else None
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Dispatch entry ID must be numeric."}), 400

    image = inspection_service.attach_demo_override_image(
        bin_snapshot,
        file_name=uploaded.filename,
        file_bytes=file_bytes,
        mime_type=mime_type,
    )
    refresh_dispatch_entry_image(
        bin_id=bin_snapshot["id"],
        entry_id=entry_id,
        image_id=image.get("image_id"),
        review_status=image.get("review_status") or "",
    )
    if last_zone:
        summarize_zone(last_zone, survey_complete=survey_complete)
    return jsonify({"status": "ok", "image": image, "message": f"Demo image attached to {bin_snapshot['label']}."})


@app.route("/dispatch-entries/<int:entry_id>", methods=["DELETE"])
def remove_dispatch_entry(entry_id):
    deleted = delete_dispatch_entry(entry_id)
    if not deleted:
        return jsonify({"status": "error", "message": "Dispatch entry not found."}), 404
    return jsonify({"status": "ok", "deleted": deleted, "dispatch": get_dispatch_snapshot()})


@app.route("/truck-dispatch", methods=["GET"])
def truck_dispatch_collection():
    return jsonify(build_truck_dispatch_payload())


@app.route("/truck-dispatch/assign", methods=["POST"])
def truck_dispatch_assign():
    data = request.json or {}
    entry_ids = data.get("entry_ids") or []
    truck_number = str(data.get("truck_number") or "").strip()
    route_number = str(data.get("route_number") or "").strip()
    if not isinstance(entry_ids, list) or not entry_ids:
        return jsonify({"status": "error", "message": "At least one dispatch entry must be selected."}), 400
    if not truck_number:
        return jsonify({"status": "error", "message": "Truck number is required."}), 400
    if not route_number:
        return jsonify({"status": "error", "message": "Route number is required."}), 400
    try:
        result = assign_dispatch_entries_to_truck(entry_ids, truck_number, route_number)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Dispatch entry IDs must be numeric."}), 400
    message = (
        f"Assigned {len(result['assigned_entry_ids'])} bin request(s) to truck {truck_number} on route {route_number}."
        if result["assigned_entry_ids"]
        else "No new dispatch requests were assigned."
    )
    return jsonify(
        {
            "status": "ok",
            "message": message,
            "assigned_entry_ids": result["assigned_entry_ids"],
            "skipped": result["skipped"],
            "batch_id": result["batch_id"],
            "truck_dispatch": result["truck_dispatch"],
            "dispatch": get_dispatch_snapshot(),
        }
    )


@app.route("/truck-dispatch/items/<int:item_id>/advance", methods=["POST"])
def truck_dispatch_advance(item_id):
    truck_dispatch = advance_truck_dispatch_item(item_id)
    if truck_dispatch is None:
        return jsonify({"status": "error", "message": "Truck dispatch item not found."}), 404
    return jsonify(
        {
            "status": "ok",
            "message": "Truck dispatch workflow advanced.",
            "truck_dispatch": truck_dispatch,
            "dispatch": get_dispatch_snapshot(),
        }
    )


@app.route("/truck-dispatch/items/<int:item_id>/api-confirmation", methods=["POST"])
def truck_dispatch_api_confirmation(item_id):
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    confirmation_status = str(data.get("confirmation_status") or "CONFIRMED").strip().upper()
    uploaded = request.files.get("image")
    try:
        if uploaded and uploaded.filename:
            file_bytes = uploaded.read()
            if not file_bytes:
                return jsonify({"status": "error", "message": "Uploaded truck closure image is empty."}), 400
            store_truck_dispatch_api_image(
                item_id,
                file_name=uploaded.filename,
                file_bytes=file_bytes,
                mime_type=uploaded.mimetype or _guess_mime_type(uploaded.filename, "image/jpeg"),
                provider_name="esp32-cam-upload",
            )
        elif data.get("image_base64"):
            decoded = decode_image_payload(
                data.get("image_base64"),
                file_name=data.get("file_name") or "esp32_capture.jpg",
                mime_type=data.get("mime_type") or "",
            )
            if decoded:
                store_truck_dispatch_api_image(
                    item_id,
                    file_name=decoded["file_name"],
                    file_bytes=decoded["file_bytes"],
                    mime_type=decoded["mime_type"],
                    provider_name="esp32-cam-base64",
                )
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Truck closure image could not be processed: {exc}"}), 400
    truck_dispatch = confirm_truck_dispatch_item_api(item_id, confirmation_status=confirmation_status)
    if truck_dispatch is None:
        return jsonify({"status": "error", "message": "Truck dispatch item not found."}), 404
    return jsonify(
        {
            "status": "ok",
            "message": "Truck pickup closure confirmed from API.",
            "truck_dispatch": truck_dispatch,
            "dispatch": get_dispatch_snapshot(),
        }
    )


@app.route("/truck-dispatch/items/<int:item_id>", methods=["DELETE"])
def truck_dispatch_delete(item_id):
    deleted = delete_truck_dispatch_item(item_id)
    if not deleted:
        return jsonify({"status": "error", "message": "Truck dispatch item not found."}), 404
    return jsonify(
        {
            "status": "ok",
            "message": f"Removed {deleted['label']} from truck dispatch.",
            "deleted": deleted,
            "truck_dispatch": list_truck_dispatch(),
            "dispatch": get_dispatch_snapshot(),
        }
    )


@app.route("/zone-summary", methods=["POST"])
def zone_summary():
    data = request.json or {}
    try:
        zone = {
            "south": float(data.get("south")),
            "north": float(data.get("north")),
            "west": float(data.get("west")),
            "east": float(data.get("east")),
        }
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Zone bounds are required."}), 400

    if zone["south"] > zone["north"] or zone["west"] > zone["east"]:
        return jsonify({"status": "error", "message": "Zone bounds are invalid."}), 400

    summary = build_pending_zone_summary(zone)
    store_zone_summary(summary)
    set_zone_mission_state(
        status="ready",
        message="Zone saved. Start the survey flight to collect dispatch details.",
        completed_waypoints=0,
        total_waypoints=len(generate_zone_scan_waypoints(zone)),
    )
    return jsonify({"status": "ok", "summary": summary})


@app.route("/zone-fly", methods=["POST"])
def zone_fly():
    data = request.json or {}
    try:
        zone = {
            "south": float(data.get("south")),
            "north": float(data.get("north")),
            "west": float(data.get("west")),
            "east": float(data.get("east")),
        }
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Zone bounds are required."}), 400

    if zone["south"] > zone["north"] or zone["west"] > zone["east"]:
        return jsonify({"status": "error", "message": "Zone bounds are invalid."}), 400

    center = zone_center(zone)
    validation = validate_zone_survey_plan(zone)
    if not validation["allowed"]:
        next_mission_id()
        reset_progress_state()
        set_nav_state("error", validation["error"])
        set_zone_mission_state(status="error", message=validation.get("details") or validation["error"])
        return jsonify({"status": "error", "message": validation["error"]}), 409

    pending_summary = build_pending_zone_summary(
        zone,
        message="Drone dispatched. Survey is in progress and dispatch summary will appear after full zone coverage.",
    )
    store_zone_summary(pending_summary)
    begin_zone_survey(zone)
    return jsonify(
        {
            "status": "ok",
            "message": f"Drone dispatched to survey the zone starting near {center['lat']:.5f}, {center['lon']:.5f}.",
            "center": center,
            "summary": pending_summary,
            "route": validation["route_points"],
            "warnings": validation.get("warnings", []),
            "rerouted": validation.get("rerouted", False),
        }
    )


@app.route("/weather", methods=["GET", "POST"])
def weather():
    if request.method == "GET":
        return jsonify(get_weather_snapshot())

    data = request.json or {}
    try:
        rain = float(data.get("rain", 0))
        wind = float(data.get("wind", 0))
        snow = float(data.get("snow", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Weather values must be numeric."}), 400

    try:
        applied = apply_weather_state(rain, wind, snow)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Failed to sync weather to AirSim: {exc}"}), 500

    set_feedback_stage(
        feedback_state["stage"],
        weather=applied,
        reason="Weather updated from user input and synced to AirSim.",
    )
    return jsonify({"status": "ok", "weather": applied})


@app.route("/speed", methods=["GET", "POST"])
def speed():
    if request.method == "GET":
        return jsonify(get_demo_snapshot())

    data = request.json or {}
    try:
        multiplier = float(data.get("multiplier", 1))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Speed multiplier must be numeric."}), 400

    applied = apply_speed_state(multiplier)
    return jsonify({"status": "ok", "demo": applied})


@app.route("/start")
def start():
    next_mission_id()
    reset_progress_state()
    set_nav_state("taking_off", "Sending takeoff command to AirSim.")
    set_zone_mission_state(
        status="idle",
        message="No zone survey has been started.",
        completed_waypoints=0,
        total_waypoints=zone_mission_state["total_waypoints"],
    )
    try:
        takeoff()
        set_nav_state("idle", "Takeoff complete.")
        return {"status": "ok", "message": nav_state["message"]}
    except Exception as exc:
        set_nav_state("error", f"Takeoff failed: {exc}")
        return jsonify({"status": "error", "message": nav_state["message"]}), 500


@app.route("/goto", methods=["POST"])
def goto():
    data = request.json or {}
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        set_nav_state("error", "Missing destination coordinates.")
        return jsonify({"status": "error", "message": nav_state["message"]}), 400

    try:
        target_point = {"lat": float(lat), "lon": float(lon)}
    except (TypeError, ValueError):
        set_nav_state("error", "Destination coordinates must be numeric.")
        return jsonify({"status": "error", "message": nav_state["message"]}), 400

    set_zone_mission_state(
        status="idle",
        message="Manual flight active. Zone survey is not running.",
        completed_waypoints=0,
        total_waypoints=0,
    )
    validation = prepare_route_validation(current_route_start(), target_point)
    if not validation["allowed"]:
        next_mission_id()
        reset_progress_state()
        set_nav_state("error", validation["error"])
        return jsonify({"status": "error", "message": validation["error"]}), 409

    if validation.get("message"):
        set_nav_state("flying", validation["message"])
    elif validation.get("warnings"):
        set_nav_state("flying", validation["warnings"][0])
    begin_navigation(target_point["lat"], target_point["lon"], route_points=validation["route_points"])
    return jsonify(
        {
            "status": "ok",
            "message": nav_state["message"],
            "route": validation["route_points"],
            "warnings": validation.get("warnings", []),
            "rerouted": validation.get("rerouted", False),
        }
    )


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
