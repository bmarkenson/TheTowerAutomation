#!/usr/bin/env python3
"""Serve TheTower's Windows-friendly browser control surface on Linux."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.automation_process import (
    DEFAULT_ADB_ENVIRONMENT_FILE,
    DEFAULT_AUTOMATION_SERVICE,
    SystemdAutomationManager,
)
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService


STATIC_DIR = Path(__file__).resolve().parent / "control_surface"
DEFAULT_TOKEN_ENV = "THETOWER_CONTROL_TOKEN"
MAX_REQUEST_BYTES = 8192


class ControlSurfaceHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying the service and optional bearer token."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        service: ControlSurfaceService,
        token: Optional[str] = None,
        static_dir: Path = STATIC_DIR,
    ) -> None:
        self.service = service
        self.token = token
        self.static_dir = static_dir.resolve()
        super().__init__(server_address, ControlSurfaceHandler)


class ControlSurfaceHandler(BaseHTTPRequestHandler):
    """Narrow JSON API plus same-origin static GUI assets."""

    server: ControlSurfaceHTTPServer
    server_version = "TheTowerControlSurface/0.1"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            if not self._authorized():
                return
            self._handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if parsed.path not in {"/api/v1/control", "/api/v1/process"}:
            self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return
        if not self._authorized():
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._json_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"JSON body must be between 1 and {MAX_REQUEST_BYTES} bytes",
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if parsed.path == "/api/v1/control":
                response = self.server.service.apply_control(payload)
            else:
                response = self.server.service.apply_process_action(payload)
        except UnicodeDecodeError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Body must be UTF-8 JSON")
            return
        except json.JSONDecodeError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Malformed JSON body")
            return
        except ControlSurfaceRequestError as exc:
            self._json_error(exc.status, str(exc))
            return
        self._send_json(HTTPStatus.OK, response)

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # Intentionally omit CORS headers. Same-origin GUI requests are the
        # only browser access supported by the control API.
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/v1/status":
                payload = self.server.service.status()
            elif path == "/api/v1/battles":
                payload = self.server.service.battles(
                    limit=_query_limit(query, default=25)
                )
            elif path.startswith("/api/v1/battles/"):
                battle_id = unquote(path.removeprefix("/api/v1/battles/"))
                payload = self.server.service.battle(battle_id)
            elif path == "/api/v1/activity":
                payload = self.server.service.activity(
                    limit=_query_limit(query, default=80),
                    levels=_query_levels(query),
                )
            else:
                self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
                return
        except ControlSurfaceRequestError as exc:
            self._json_error(exc.status, str(exc))
            return
        except (TypeError, ValueError):
            self._json_error(HTTPStatus.BAD_REQUEST, "limit must be an integer")
            return
        self._send_json(HTTPStatus.OK, payload)

    def _serve_static(self, path: str) -> None:
        routes = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        asset = routes.get(path)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        filename, content_type = asset
        candidate = (self.server.static_dir / filename).resolve()
        if candidate.parent != self.server.static_dir or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            content = candidate.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _authorized(self) -> bool:
        expected = self.server.token
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        accepted = supplied.startswith(prefix) and secrets.compare_digest(
            supplied[len(prefix) :], expected
        )
        if accepted:
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self._security_headers()
        self.send_header("WWW-Authenticate", 'Bearer realm="TheTower control surface"')
        body = json.dumps({"error": "Bearer token required"}).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _send_json(self, status: int, payload: Any) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )

    def log_message(self, format: str, *args: object) -> None:
        print(f"[CONTROL_SURFACE_HTTP] {self.address_string()} {format % args}")


def _query_limit(query: dict[str, list[str]], *, default: int) -> int:
    values = query.get("limit")
    return default if not values else int(values[-1])


def _query_levels(query: dict[str, list[str]]) -> Optional[list[str]]:
    values = [
        level.strip()
        for raw_value in query.get("levels", [])
        for level in raw_value.split(",")
        if level.strip()
    ]
    return values or None


def _is_loopback(bind: str) -> bool:
    normalized = bind.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve TheTower's browser control surface"
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--repository-root", default=str(PROJECT_ROOT))
    parser.add_argument("--control-file", default="logs/automation_ctl.json")
    parser.add_argument("--action-log", default="logs/actions.log")
    parser.add_argument("--battles-dir", default="logs/battles")
    parser.add_argument("--tournaments-dir", default="logs/tournaments")
    parser.add_argument("--stale-after-seconds", type=int, default=180)
    parser.add_argument(
        "--automation-service",
        default=DEFAULT_AUTOMATION_SERVICE,
        help=(
            "Fixed systemd user service controlled by process lifecycle requests "
            f"(default: {DEFAULT_AUTOMATION_SERVICE})"
        ),
    )
    parser.add_argument(
        "--automation-adb-environment-file",
        default=str(DEFAULT_ADB_ENVIRONMENT_FILE),
        help=(
            "Persistent EnvironmentFile used by the managed automation unit "
            f"(default: {DEFAULT_ADB_ENVIRONMENT_FILE})"
        ),
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"Environment variable holding the bearer token (default: {DEFAULT_TOKEN_ENV})",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.stale_after_seconds <= 0:
        parser.error("--stale-after-seconds must be positive")
    try:
        SystemdAutomationManager(
            args.automation_service,
            adb_environment_file=args.automation_adb_environment_file,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    token = os.getenv(args.token_env) or None
    if not _is_loopback(args.bind) and not token:
        print(
            f"Refusing non-loopback bind {args.bind!r} without a bearer token in "
            f"{args.token_env}. Prefer the documented SSH tunnel instead.",
            file=sys.stderr,
        )
        return 2
    if token and len(token) < 24:
        print(
            f"Refusing bearer token from {args.token_env}: use at least 24 characters.",
            file=sys.stderr,
        )
        return 2

    process_manager = SystemdAutomationManager(
        args.automation_service,
        adb_environment_file=args.automation_adb_environment_file,
    )
    service = ControlSurfaceService(
        repository_root=args.repository_root,
        control_file=args.control_file,
        action_log=args.action_log,
        battles_dir=args.battles_dir,
        tournaments_dir=args.tournaments_dir,
        stale_after_seconds=args.stale_after_seconds,
        process_manager=process_manager,
    )
    try:
        server = ControlSurfaceHTTPServer(
            (args.bind, args.port),
            service=service,
            token=token,
        )
    except OSError as exc:
        print(f"Unable to start control surface: {exc}", file=sys.stderr)
        return 1

    host, port = server.server_address[:2]
    auth_label = f"bearer token from {args.token_env}" if token else "loopback only"
    print(f"TheTower control surface listening on http://{host}:{port} ({auth_label})")
    if _is_loopback(args.bind):
        print(
            f"From Windows, tunnel with: ssh -N -L {port}:127.0.0.1:{port} "
            "<linux-user>@<linux-host>"
        )
        print(f"Then open http://127.0.0.1:{port}/ in the Windows browser.")
    else:
        print(
            "WARNING: this listener uses plain HTTP. Put it behind TLS or use "
            "only on a protected network.",
            file=sys.stderr,
        )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Stopping TheTower control surface.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ControlSurfaceHTTPServer",
    "ControlSurfaceHandler",
    "main",
    "parse_args",
]
