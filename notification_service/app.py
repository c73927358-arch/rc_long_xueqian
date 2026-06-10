import argparse
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from .database import database
from .http_handler import NotificationHandler
from .security import origin_from_parsed, parse_allowed_target_origins
from .settings import DB_PATH, PUBLIC_DIR, delivery_timeout_seconds, delivering_lease_seconds, notification_worker_concurrency
from .worker import delivery_worker_pool


def log_startup_self_check(worker_concurrency):
    try:
        allowed_targets = parse_allowed_target_origins()
        allowed_targets_label = "not set" if allowed_targets is None else ", ".join(sorted(allowed_targets))
    except ValueError as exc:
        allowed_targets_label = f"invalid: {exc}"
    print(f"Startup self-check: database.path={DB_PATH}")
    print(f"Startup self-check: public.exists={PUBLIC_DIR.exists()} path={PUBLIC_DIR}")
    print(f"Startup self-check: allowedTargets={allowed_targets_label}")
    print(f"Startup self-check: deliveryTimeoutSeconds={delivery_timeout_seconds()}")
    print(f"Startup self-check: deliveringLeaseSeconds={delivering_lease_seconds()}")
    print(f"Startup self-check: workerConcurrency={worker_concurrency}")


def create_arg_parser():
    parser = argparse.ArgumentParser(description="Internal HTTP notification delivery demo service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    return parser


def run(host="127.0.0.1", port=8000):
    # Validate the advertised service origin early, so startup fails clearly for bad host/port input.
    origin_from_parsed(urlparse(f"http://{host}:{port}"))
    worker_concurrency = notification_worker_concurrency()
    database.initialize()
    log_startup_self_check(worker_concurrency)
    delivery_worker_pool.start(worker_concurrency)

    server = ThreadingHTTPServer((host, port), NotificationHandler)
    print(f"Notification service running at http://{host}:{port}")
    print(f"Frontend: http://{host}:{port}/")
    print(f"Mock vendor: http://{host}:{port}/mock/vendor/crm")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        delivery_worker_pool.stop()
        server.server_close()


def main():
    args = create_arg_parser().parse_args()
    run(host=args.host, port=args.port)
