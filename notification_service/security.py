import ipaddress
import json
import os
import socket
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, build_opener

from .settings import REDACTED_VALUE, SENSITIVE_KEY_RE, URL_QUERY_RE, env_flag


class InvalidTargetError(ValueError):
    pass

def is_sensitive_key(key):
    return bool(SENSITIVE_KEY_RE.search(str(key or "")))

def redact_query_secrets(value):
    if not value:
        return value

    def replace(match):
        separator, key, raw_value = match.groups()
        if is_sensitive_key(key):
            return f"{separator}{key}={REDACTED_VALUE}"
        return f"{separator}{key}={raw_value}"

    return URL_QUERY_RE.sub(replace, str(value))

def redact_sensitive_json(value):
    if isinstance(value, dict):
        return {
            key: REDACTED_VALUE if is_sensitive_key(key) else redact_sensitive_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_json(item) for item in value]
    return value

def redact_headers(headers):
    return {
        key: REDACTED_VALUE if is_sensitive_key(key) else value
        for key, value in headers.items()
    }

def redact_body_for_api(body):
    try:
        parsed = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return body
    return json.dumps(redact_sensitive_json(parsed), ensure_ascii=False, separators=(",", ":"))

def body_preview_for_api(body):
    redacted = redact_body_for_api(body)
    return redacted[:160] + ("..." if len(redacted) > 160 else "")

def parse_allowed_target_origins():
    raw = os.environ.get("NOTIFICATION_ALLOWED_TARGETS", "").strip()
    if not raw:
        return None
    origins = set()
    for item in raw.split(","):
        origin = item.strip()
        if not origin:
            continue
        parsed = urlparse(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("NOTIFICATION_ALLOWED_TARGETS must contain comma-separated exact http(s) origins")
        origins.add(origin_from_parsed(parsed))
    return origins

def origin_from_parsed(parsed):
    host = parsed.hostname.lower() if parsed.hostname else ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    port_part = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme.lower()}://{host}{port_part}"

def origin_from_host_header(host_header):
    if not host_header:
        return None
    parsed = urlparse(f"http://{host_header.strip()}")
    if not parsed.hostname:
        return None
    try:
        return origin_from_parsed(parsed)
    except ValueError:
        return None

def current_request_origin(handler):
    host_origin = origin_from_host_header(handler.headers.get("Host"))
    if host_origin:
        return host_origin

    server_host, server_port = handler.server.server_address[:2]
    host = server_host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    parsed = urlparse(f"http://{host}:{server_port}")
    return origin_from_parsed(parsed)

def is_same_origin_mock_vendor(parsed, current_origin):
    if not env_flag("ALLOW_LOCAL_MOCK_VENDOR", default=True):
        return False
    try:
        return origin_from_parsed(parsed) == current_origin and parsed.path.startswith("/mock/vendor/")
    except ValueError:
        return False

def delivery_validation_origin(target_url):
    parsed = urlparse(target_url)
    try:
        target_origin = origin_from_parsed(parsed)
    except ValueError:
        return None
    if is_same_origin_mock_vendor(parsed, target_origin):
        return target_origin
    return None

def resolve_target_addresses(hostname, port):
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        return [literal]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"targetUrl hostname could not be resolved: {exc}") from exc

    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise ValueError("targetUrl hostname resolved to no usable addresses")
    return addresses

def is_blocked_target_address(address):
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )

def assert_public_resolved_target(parsed):
    for address in resolve_target_addresses(parsed.hostname, parsed.port):
        if is_blocked_target_address(address):
            raise ValueError(f"targetUrl resolves to blocked SSRF address {address}")

def validate_target_url(value, current_origin=None):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("targetUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("targetUrl must not include username or password")
    try:
        origin = origin_from_parsed(parsed)
    except ValueError as exc:
        raise ValueError("targetUrl has an invalid host or port") from exc

    if current_origin and is_same_origin_mock_vendor(parsed, current_origin):
        return value

    allowed_origins = parse_allowed_target_origins()
    if allowed_origins is not None and origin not in allowed_origins:
        raise ValueError("targetUrl origin is not allowed by NOTIFICATION_ALLOWED_TARGETS")

    assert_public_resolved_target(parsed)
    return value

class SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, current_origin=None):
        super().__init__()
        self.current_origin = current_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urljoin(req.full_url, newurl)
        try:
            validate_target_url(redirect_url, current_origin=self.current_origin)
        except ValueError as exc:
            raise InvalidTargetError(f"redirect target blocked: {exc}") from exc
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)

def build_safe_delivery_opener(current_origin=None):
    return build_opener(SafeRedirectHandler(current_origin=current_origin))


class TargetSecurityPolicy:
    """Policy object that owns target URL validation and safe redirect handling."""

    def validate(self, target_url, current_origin=None):
        return validate_target_url(target_url, current_origin=current_origin)

    def delivery_origin(self, target_url):
        return delivery_validation_origin(target_url)

    def opener(self, current_origin=None):
        return build_safe_delivery_opener(current_origin=current_origin)


target_security_policy = TargetSecurityPolicy()
