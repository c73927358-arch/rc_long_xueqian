import json
import mimetypes
import re
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .auth import authenticator
from .metrics import health_reporter
from .security import current_request_origin
from .service import notification_service, parse_manual_action_payload
from .settings import DB_PATH, PUBLIC_DIR, SCHEMA_VERSION, SERVICE_VERSION
from .time_utils import format_ts, now_ts


class NotificationHandler(BaseHTTPRequestHandler):
    server_version = "NotificationDemo/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_common_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                self.write_json(200, health_reporter.health())
            except Exception as exc:
                self.write_json(
                    500,
                    {
                        "status": "degraded",
                        "serviceVersion": SERVICE_VERSION,
                        "schemaVersion": SCHEMA_VERSION,
                        "database": {
                            "path": str(DB_PATH),
                            "ok": False,
                        },
                        "error": str(exc),
                        "now": format_ts(now_ts()),
                    },
                )
            return
        if parsed.path == "/api/stats":
            self.write_json(200, health_reporter.stats())
            return
        if parsed.path == "/api/notifications":
            query = parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            limit = query.get("limit", ["50"])[0]
            offset = query.get("offset", ["0"])[0]
            event_type = query.get("eventType", [None])[0]
            source_system = query.get("sourceSystem", [None])[0]
            target_url = query.get("targetUrl", [None])[0]
            sort = query.get("sort", [None])[0]
            order = query.get("order", [None])[0]
            created_from = query.get("createdFrom", [None])[0]
            created_to = query.get("createdTo", [None])[0]
            updated_from = query.get("updatedFrom", [None])[0]
            updated_to = query.get("updatedTo", [None])[0]
            try:
                result = notification_service.list(
                    status=status,
                    limit=limit,
                    offset=offset,
                    event_type=event_type,
                    source_system=source_system,
                    target_url=target_url,
                    sort=sort,
                    order=order,
                    created_from=created_from,
                    created_to=created_to,
                    updated_from=updated_from,
                    updated_to=updated_to,
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(200, result)
            return
        if parsed.path == "/api/notifications/export.csv":
            query = parse_qs(parsed.query)
            try:
                content = notification_service.export_csv(
                    status=query.get("status", [None])[0],
                    limit=query.get("limit", ["1000"])[0],
                    offset=query.get("offset", ["0"])[0],
                    event_type=query.get("eventType", [None])[0],
                    source_system=query.get("sourceSystem", [None])[0],
                    target_url=query.get("targetUrl", [None])[0],
                    sort=query.get("sort", [None])[0],
                    order=query.get("order", [None])[0],
                    created_from=query.get("createdFrom", [None])[0],
                    created_to=query.get("createdTo", [None])[0],
                    updated_from=query.get("updatedFrom", [None])[0],
                    updated_to=query.get("updatedTo", [None])[0],
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_text(
                200,
                content,
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="notifications.csv"'},
            )
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/attempts"):
            notification_id = parsed.path.split("/")[-2]
            items = notification_service.attempts(notification_id)
            if items is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, {"items": items})
            return
        if parsed.path.startswith("/api/notifications/"):
            notification_id = parsed.path.rsplit("/", 1)[-1]
            item = notification_service.get(notification_id)
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/mock/vendor/"):
            self.handle_mock_vendor(parsed)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if authenticator.is_protected_path(parsed.path) and not authenticator.is_authorized(self.headers):
            self.write_json(401, {"error": "unauthorized"})
            return
        if parsed.path == "/api/notifications":
            try:
                payload = self.read_json_body()
                item, duplicate = notification_service.create(payload, current_origin=current_request_origin(self))
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(
                201 if not duplicate else 200,
                {
                    "id": item["id"],
                    "requestId": item["requestId"],
                    "status": item["status"],
                    "duplicate": duplicate,
                    "duplicated": duplicate,
                    "idempotent": duplicate,
                    "idempotency": "reused_existing" if duplicate else "created",
                },
            )
            return
        if parsed.path == "/api/notifications/retry":
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                result = notification_service.retry_batch(
                    status=str(payload.get("status") or "failed"),
                    limit=payload.get("limit", 50),
                    handled_by=handled_by,
                    note=note,
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(200, result)
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/dead-letter"):
            notification_id = parsed.path.split("/")[-2]
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                item = notification_service.dead_letter(
                    notification_id,
                    handled_by=handled_by,
                    note=note,
                )
            except ValueError as exc:
                self.write_json(409, {"error": str(exc)})
                return
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/retry"):
            notification_id = parsed.path.split("/")[-2]
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                item = notification_service.retry_one(
                    notification_id,
                    handled_by=handled_by,
                    note=note,
                )
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
        delay_ms_raw = query.get("delayMs", ["0"])[0]
        try:
            delay_ms = max(int(delay_ms_raw), 0)
        except (TypeError, ValueError):
            delay_ms = 0
        if delay_ms:
            time.sleep(delay_ms / 1000)

        redirect_target = query.get("redirectTo", [None])[0]
        redirect_mode = query.get("redirect", [None])[0]
        if redirect_mode == "blocked":
            redirect_target = f"{current_request_origin(self)}/not-mock/redirect-blocked"
        elif redirect_mode == "mock":
            redirect_target = f"{current_request_origin(self)}/mock/vendor/{vendor_name}-redirected"
        if redirect_target:
            redirect_status_raw = query.get("redirectStatus", ["302"])[0]
            try:
                redirect_status = int(redirect_status_raw)
            except (TypeError, ValueError):
                redirect_status = 302
            if redirect_status not in {301, 302, 303, 307, 308}:
                redirect_status = 302
            payload = {
                "vendor": vendor_name,
                "received": True,
                "status": redirect_status,
                "method": self.command,
                "redirectTo": redirect_target,
                "body": body,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(redirect_status)
            self.add_common_headers()
            self.send_header("Location", redirect_target)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        should_fail = query.get("fail", ["0"])[0] in {"1", "true", "yes"}
        should_fail = should_fail or self.headers.get("X-Mock-Fail", "").lower() in {"1", "true", "yes"}
        status_raw = query.get("status", [None])[0]
        status_code = 500 if should_fail else 200
        if status_raw and re.fullmatch(r"\d{3}", status_raw):
            status_code = int(status_raw)
        received = 200 <= status_code < 300
        if should_fail:
            self.write_json(
                status_code,
                {
                    "vendor": vendor_name,
                    "received": received,
                    "message": "mock failure",
                    "status": status_code,
                    "method": self.command,
                    "body": body,
                },
            )
            return
        self.write_json(
            status_code,
            {
                "vendor": vendor_name,
                "received": received,
                "status": status_code,
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

    def write_text(self, status_code, text, content_type, extra_headers=None):
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def add_common_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Notification-Api-Key, X-Requested-With",
        )

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")
