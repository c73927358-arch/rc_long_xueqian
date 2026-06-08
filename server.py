#!/usr/bin/env python3
import argparse
import json
import mimetypes
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"
DB_PATH = ROOT_DIR / "notifications.db"
DB_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
ALLOWED_METHODS = {"POST", "PUT", "PATCH"}
READY_STATUSES = {"queued", "waiting_retry"}


def now_ts():
    return time.time()


def format_ts(value):
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                request_id TEXT UNIQUE,
                event_type TEXT,
                source_system TEXT,
                target_url TEXT NOT NULL,
                method TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                next_attempt_at REAL,
                last_error TEXT,
                last_status_code INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_ready ON notifications(status, next_attempt_at, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")
        conn.execute(
            """
            UPDATE notifications
               SET status = 'queued',
                   next_attempt_at = ?,
                   updated_at = ?,
                   last_error = 'service restarted while delivery was in progress'
             WHERE status = 'delivering'
            """,
            (timestamp, timestamp),
        )


def row_to_dict(row, include_body=True):
    if row is None:
        return None
    body = row["body"]
    item = {
        "id": row["id"],
        "requestId": row["request_id"],
        "eventType": row["event_type"],
        "sourceSystem": row["source_system"],
        "targetUrl": row["target_url"],
        "method": row["method"],
        "headers": json.loads(row["headers_json"] or "{}"),
        "status": row["status"],
        "attemptCount": row["attempt_count"],
        "maxAttempts": row["max_attempts"],
        "nextAttemptAt": format_ts(row["next_attempt_at"]),
        "lastError": row["last_error"],
        "lastStatusCode": row["last_status_code"],
        "createdAt": format_ts(row["created_at"]),
        "updatedAt": format_ts(row["updated_at"]),
        "deliveredAt": format_ts(row["delivered_at"]),
    }
    if include_body:
        item["body"] = body
    else:
        item["bodyPreview"] = body[:160] + ("..." if len(body) > 160 else "")
    return item


def validate_target_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("targetUrl must be an absolute http(s) URL")
    return value


def normalize_headers(headers):
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object")
    normalized = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("header names must be non-empty strings")
        normalized[key.strip()] = str(value)
    return normalized


def normalize_body(body, headers):
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, (dict, list, int, float, bool)):
        has_content_type = any(key.lower() == "content-type" for key in headers)
        if not has_content_type:
            headers["Content-Type"] = "application/json"
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    raise ValueError("body must be an object, array, string, number, boolean, or null")


def clamp_max_attempts(value):
    if value is None:
        return 5
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("maxAttempts must be an integer") from exc
    return min(max(parsed, 1), 10)


def create_notification(payload):
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    target_url = validate_target_url(str(payload.get("targetUrl", "")).strip())
    method = str(payload.get("method") or "POST").upper()
    if method not in ALLOWED_METHODS:
        raise ValueError("method must be one of POST, PUT, PATCH")
    headers = normalize_headers(payload.get("headers"))
    body = normalize_body(payload.get("body"), headers)
    max_attempts = clamp_max_attempts(payload.get("maxAttempts"))
    request_id = payload.get("requestId")
    if request_id is not None:
        request_id = str(request_id).strip() or None

    notification_id = str(uuid.uuid4())
    timestamp = now_ts()
    values = (
        notification_id,
        request_id,
        str(payload.get("eventType") or "").strip() or None,
        str(payload.get("sourceSystem") or "").strip() or None,
        target_url,
        method,
        json.dumps(headers, ensure_ascii=False),
        body,
        "queued",
        0,
        max_attempts,
        timestamp,
        None,
        None,
        timestamp,
        timestamp,
        None,
    )

    with DB_LOCK, get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO notifications (
                    id, request_id, event_type, source_system, target_url, method,
                    headers_json, body, status, attempt_count, max_attempts,
                    next_attempt_at, last_error, last_status_code, created_at, updated_at, delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        except sqlite3.IntegrityError:
            if request_id is None:
                raise
            row = conn.execute("SELECT * FROM notifications WHERE request_id = ?", (request_id,)).fetchone()
            return row_to_dict(row), True
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        return row_to_dict(row), False


def list_notifications(status=None, limit=50):
    limit = min(max(int(limit), 1), 200)
    with DB_LOCK, get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [row_to_dict(row, include_body=False) for row in rows]


def get_notification(notification_id):
    with DB_LOCK, get_db() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return row_to_dict(row)


def retry_notification(notification_id):
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "delivering":
            raise ValueError("notification is delivering and cannot be retried right now")
        conn.execute(
            """
            UPDATE notifications
               SET status = 'queued',
                   attempt_count = 0,
                   next_attempt_at = ?,
                   last_error = NULL,
                   last_status_code = NULL,
                   delivered_at = NULL,
                   updated_at = ?
             WHERE id = ?
            """,
            (timestamp, timestamp, notification_id),
        )
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return row_to_dict(row)


def backoff_seconds(attempt_count):
    return min(5 * (2 ** max(attempt_count - 1, 0)), 300)


def claim_next_job():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM notifications
             WHERE status IN ('queued', 'waiting_retry')
               AND next_attempt_at <= ?
               AND attempt_count < max_attempts
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT 1
            """,
            (timestamp,),
        ).fetchone()
        if row is None:
            return None
        updated = conn.execute(
            """
            UPDATE notifications
               SET status = 'delivering',
                   attempt_count = attempt_count + 1,
                   updated_at = ?
             WHERE id = ?
               AND status IN ('queued', 'waiting_retry')
            """,
            (timestamp, row["id"]),
        ).rowcount
        if updated == 0:
            return None
        return conn.execute("SELECT * FROM notifications WHERE id = ?", (row["id"],)).fetchone()


def mark_success(notification_id, status_code):
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            UPDATE notifications
               SET status = 'succeeded',
                   last_error = NULL,
                   last_status_code = ?,
                   delivered_at = ?,
                   updated_at = ?,
                   next_attempt_at = NULL
             WHERE id = ?
            """,
            (status_code, timestamp, timestamp, notification_id),
        )


def mark_failure(row, status_code, error):
    timestamp = now_ts()
    attempt_count = int(row["attempt_count"])
    max_attempts = int(row["max_attempts"])
    if attempt_count >= max_attempts:
        status = "failed"
        next_attempt_at = None
    else:
        status = "waiting_retry"
        next_attempt_at = timestamp + backoff_seconds(attempt_count)

    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            UPDATE notifications
               SET status = ?,
                   last_error = ?,
                   last_status_code = ?,
                   next_attempt_at = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (status, error[:1000], status_code, next_attempt_at, timestamp, row["id"]),
        )


def deliver_job(row):
    headers = json.loads(row["headers_json"] or "{}")
    body_bytes = row["body"].encode("utf-8")
    request = Request(
        row["target_url"],
        data=body_bytes,
        headers=headers,
        method=row["method"],
    )
    try:
        with urlopen(request, timeout=8) as response:
            status_code = response.getcode()
            if 200 <= status_code < 300:
                mark_success(row["id"], status_code)
            else:
                mark_failure(row, status_code, f"target returned HTTP {status_code}")
    except HTTPError as exc:
        mark_failure(row, exc.code, f"target returned HTTP {exc.code}")
    except URLError as exc:
        mark_failure(row, None, f"network error: {exc.reason}")
    except Exception as exc:
        mark_failure(row, None, f"delivery error: {exc}")


def worker_loop():
    while not STOP_EVENT.is_set():
        row = claim_next_job()
        if row is None:
            STOP_EVENT.wait(0.8)
            continue
        deliver_job(row)


class NotificationHandler(BaseHTTPRequestHandler):
    server_version = "NotificationDemo/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_common_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.write_json(200, {"status": "ok"})
            return
        if parsed.path == "/api/notifications":
            query = parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            limit = query.get("limit", ["50"])[0]
            try:
                items = list_notifications(status=status, limit=limit)
            except ValueError:
                self.write_json(400, {"error": "limit must be a number"})
                return
            self.write_json(200, {"items": items})
            return
        if parsed.path.startswith("/api/notifications/"):
            notification_id = parsed.path.rsplit("/", 1)[-1]
            item = get_notification(notification_id)
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/notifications":
            try:
                payload = self.read_json_body()
                item, duplicate = create_notification(payload)
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(201 if not duplicate else 200, {"id": item["id"], "status": item["status"], "duplicate": duplicate})
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/retry"):
            notification_id = parsed.path.split("/")[-2]
            try:
                item = retry_notification(notification_id)
            except ValueError as exc:
                self.write_json(409, {"error": str(exc)})
                return
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/mock/vendor/"):
            self.handle_mock_vendor(parsed)
            return
        self.write_json(404, {"error": "not found"})

    def do_PUT(self):
        self.do_POST()

    def do_PATCH(self):
        self.do_POST()

    def handle_mock_vendor(self, parsed):
        vendor_name = parsed.path.rsplit("/", 1)[-1] or "unknown"
        query = parse_qs(parsed.query)
        body = self.read_raw_body().decode("utf-8", errors="replace")
        should_fail = query.get("fail", ["0"])[0] in {"1", "true", "yes"}
        should_fail = should_fail or self.headers.get("X-Mock-Fail", "").lower() in {"1", "true", "yes"}
        if should_fail:
            self.write_json(500, {"vendor": vendor_name, "received": False, "message": "mock failure"})
            return
        self.write_json(
            200,
            {
                "vendor": vendor_name,
                "received": True,
                "method": self.command,
                "body": body,
            },
        )

    def read_raw_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise ValueError("request body is too large")
        return self.rfile.read(length) if length else b""

    def read_json_body(self):
        try:
            raw = self.read_raw_body()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc

    def serve_static(self, path):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (PUBLIC_DIR / relative).resolve()
        public_root = PUBLIC_DIR.resolve()
        if public_root not in file_path.parents and file_path != public_root:
            self.write_json(403, {"error": "forbidden"})
            return
        if not file_path.exists() or file_path.is_dir():
            self.write_json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, status_code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def add_common_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="Internal HTTP notification delivery demo service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    init_db()
    worker = threading.Thread(target=worker_loop, name="notification-worker", daemon=True)
    worker.start()

    server = ThreadingHTTPServer((args.host, args.port), NotificationHandler)
    print(f"Notification service running at http://{args.host}:{args.port}")
    print(f"Frontend: http://{args.host}:{args.port}/")
    print(f"Mock vendor: http://{args.host}:{args.port}/mock/vendor/crm")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
