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
import threading
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.adb_connection import PersistentAdbConnectionManager
from core.automation_process import (
    DEFAULT_ADB_ENVIRONMENT_FILE,
    DEFAULT_AUTOMATION_SERVICE,
    AutomationProcessError,
    SystemdAutomationManager,
)
from core.control_surface import (
    DEFAULT_DISCARDED_BATTLE_RETENTION_DAYS,
    ControlSurfaceRequestError,
    ControlSurfaceService,
)
from core.host_performance import DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS


STATIC_DIR = Path(__file__).resolve().parent / "control_surface"
DEFAULT_TOKEN_ENV = "THETOWER_CONTROL_TOKEN"
MAX_REQUEST_BYTES = 8192
MAX_HOST_PERFORMANCE_REQUEST_BYTES = 512 * 1024
DISCARD_PURGE_INTERVAL_SECONDS = 6 * 60 * 60
SAVE_MAPPING_RECONCILIATION_INTERVAL_SECONDS = 5
SAVE_MAPPING_RECONCILIATION_MAX_BACKOFF_SECONDS = 5 * 60


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
        if parsed.path not in {
            "/api/v1/control",
            "/api/v1/process",
            "/api/v1/strategy-authoring",
            "/api/v1/setup-capture",
            "/api/v1/save-mapping-integration",
            "/api/v1/strategy-profiles",
            "/api/v1/host-performance",
            "/api/v1/interactive-development-lease",
            "/api/v1/host-maintenance",
        }:
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
        maximum_length = (
            MAX_HOST_PERFORMANCE_REQUEST_BYTES
            if parsed.path == "/api/v1/host-performance"
            else MAX_REQUEST_BYTES
        )
        if length <= 0 or length > maximum_length:
            self._json_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"JSON body must be between 1 and {maximum_length} bytes",
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if parsed.path == "/api/v1/control":
                response = self.server.service.apply_control(payload)
            elif parsed.path == "/api/v1/process":
                response = self.server.service.apply_process_action(payload)
            elif parsed.path == "/api/v1/strategy-profiles":
                response = self.server.service.apply_strategy_profile(payload)
            elif parsed.path == "/api/v1/strategy-authoring":
                response = self.server.service.apply_strategy_authoring(payload)
            elif parsed.path == "/api/v1/setup-capture":
                response = self.server.service.apply_setup_capture(payload)
            elif parsed.path == "/api/v1/save-mapping-integration":
                response = (
                    self.server.service.apply_save_mapping_integration(payload)
                )
            elif parsed.path == "/api/v1/interactive-development-lease":
                response = (
                    self.server.service.apply_interactive_development_lease(
                        payload
                    )
                )
            elif parsed.path == "/api/v1/host-maintenance":
                response = self.server.service.apply_host_maintenance(payload)
            else:
                response = self.server.service.publish_host_performance(payload)
        except UnicodeDecodeError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Body must be UTF-8 JSON")
            return
        except json.JSONDecodeError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Malformed JSON body")
            return
        except ControlSurfaceRequestError as exc:
            self._json_error(
                exc.status,
                str(exc),
                code=exc.code,
                details=exc.details,
            )
            return
        self._send_json(HTTPStatus.OK, response)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/v1/battles/"):
            self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return
        if not self._authorized():
            return
        battle_id = unquote(parsed.path.removeprefix("/api/v1/battles/"))
        try:
            response = self.server.service.discard_battle(battle_id)
        except ControlSurfaceRequestError as exc:
            self._json_error(
                exc.status,
                str(exc),
                code=exc.code,
                details=exc.details,
            )
            return
        self._send_json(HTTPStatus.OK, response)

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # Intentionally omit CORS headers. Same-origin GUI requests are the
        # only browser access supported by the control API.
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
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
            elif path == "/api/v1/strategy-profiles":
                payload = self.server.service.strategy_profiles()
            elif path == "/api/v1/strategy-authoring":
                payload = self.server.service.strategy_authoring()
            elif path == "/api/v1/setup-capture":
                payload = self.server.service.setup_capture()
            elif path == "/api/v1/save-mapping-integration":
                payload = self.server.service.save_mapping_integration()
            elif path.startswith("/api/v1/setup-capture/drafts/"):
                strategy_id = unquote(
                    path.removeprefix("/api/v1/setup-capture/drafts/")
                )
                if not strategy_id or "/" in strategy_id:
                    self._json_error(
                        HTTPStatus.NOT_FOUND,
                        "Captured draft endpoint not found",
                    )
                    return
                payload = self.server.service.captured_setup_draft(
                    strategy_id
                )
            elif path == "/api/v1/strategy-authoring/history":
                payload = self.server.service.strategy_history()
            elif path.startswith("/api/v1/strategy-authoring/history/"):
                suffix = path.removeprefix(
                    "/api/v1/strategy-authoring/history/"
                )
                parts = [unquote(item) for item in suffix.split("/")]
                if len(parts) == 1 and parts[0]:
                    payload = self.server.service.strategy_history(parts[0])
                elif len(parts) == 2 and all(parts):
                    payload = self.server.service.strategy_revision(
                        parts[0],
                        parts[1],
                    )
                else:
                    self._json_error(
                        HTTPStatus.NOT_FOUND,
                        "Strategy history endpoint not found",
                    )
                    return
            elif path == "/api/v1/activity":
                payload = self.server.service.activity(
                    limit=_query_limit(query, default=80),
                    levels=_query_levels(query),
                    scope=_query_value(query, "scope") or "all",
                    after=_query_value(query, "after"),
                )
            else:
                self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
                return
        except ControlSurfaceRequestError as exc:
            self._json_error(
                exc.status,
                str(exc),
                code=exc.code,
                details=exc.details,
            )
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
            "/client_model.js": (
                "client_model.js",
                "text/javascript; charset=utf-8",
            ),
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

    def _json_error(
        self,
        status: int,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {"error": message}
        if code:
            payload["code"] = code
        if details:
            payload["details"] = dict(details)
        self._send_json(status, payload)

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


def _query_value(query: dict[str, list[str]], name: str) -> Optional[str]:
    values = query.get(name)
    return None if not values else values[-1]


def _is_loopback(bind: str) -> bool:
    normalized = bind.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _discard_retention_loop(
    service: ControlSurfaceService,
    stop_event: threading.Event,
) -> None:
    """Purge expired quarantines even when no client is requesting history."""

    while not stop_event.is_set():
        try:
            service.purge_expired_discarded_battles()
        except Exception as exc:
            print(f"Discard retention sweep failed: {exc}", file=sys.stderr)
        if stop_event.wait(DISCARD_PURGE_INTERVAL_SECONDS):
            return


def _save_mapping_reconciliation_loop(
    service: ControlSurfaceService,
    stop_event: threading.Event,
) -> None:
    """Consume reviewed or machine-verified mapping work until it closes."""

    delay = 0
    while not stop_event.wait(delay):
        try:
            result = service.reconcile_save_mapping_integration()
        except Exception as exc:
            print(
                f"Save-mapping reconciliation failed: {exc}",
                file=sys.stderr,
            )
            result = {"needed": True, "disposition": "unconfirmed"}
        if result.get("needed") is True or result.get("disposition") in {
            "promotion_queued",
            "failed",
            "unconfirmed",
        }:
            delay = min(
                max(
                    SAVE_MAPPING_RECONCILIATION_INTERVAL_SECONDS,
                    delay * 2,
                ),
                SAVE_MAPPING_RECONCILIATION_MAX_BACKOFF_SECONDS,
            )
        else:
            delay = SAVE_MAPPING_RECONCILIATION_INTERVAL_SECONDS


def _persistent_adb_target_provider(
    process_manager: SystemdAutomationManager,
) -> Callable[[], str]:
    """Bind connection ownership to the installed managed-runtime contract."""

    owner_verified = False

    def configured_target() -> str:
        nonlocal owner_verified
        if not owner_verified:
            installation_status = process_manager.status()
            owner_error = installation_status.get("adb_connection_owner_error")
            if installation_status.get("available") is not True:
                owner_error = installation_status.get("error") or (
                    "Unable to verify the installed automation service's "
                    "persistent ADB connection owner"
                )
            if owner_error:
                raise AutomationProcessError(str(owner_error))
            owner_verified = True
        return process_manager.adb_connection_target()

    return configured_target


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve TheTower's browser control surface"
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--repository-root", default=str(PROJECT_ROOT))
    parser.add_argument("--control-file", default="logs/automation_ctl.json")
    parser.add_argument("--action-log", default="logs/actions.log")
    parser.add_argument(
        "--module-preset-directory",
        default="config/loadouts/custom/modules",
        help=(
            "Server-owned custom Module preset directory, relative to the "
            "repository root unless absolute"
        ),
    )
    parser.add_argument("--battles-dir", default="logs/battles")
    parser.add_argument("--tournaments-dir", default="logs/tournaments")
    parser.add_argument(
        "--discarded-battles-dir",
        default="logs/discarded_battles",
    )
    parser.add_argument(
        "--discarded-battle-retention-days",
        type=int,
        default=DEFAULT_DISCARDED_BATTLE_RETENTION_DAYS,
    )
    parser.add_argument(
        "--host-performance-db",
        default="logs/host_performance.sqlite3",
    )
    parser.add_argument(
        "--host-performance-retention-days",
        type=int,
        default=DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS,
    )
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
    if args.discarded_battle_retention_days <= 0:
        parser.error("--discarded-battle-retention-days must be positive")
    if args.host_performance_retention_days <= 0:
        parser.error("--host-performance-retention-days must be positive")
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

    strategy_profile_dir = (
        Path(args.repository_root).expanduser()
        / "config"
        / "strategies"
        / "custom"
    )
    process_manager = SystemdAutomationManager(
        args.automation_service,
        adb_environment_file=args.automation_adb_environment_file,
        strategy_profile_dir=strategy_profile_dir,
    )
    adb_connection_manager = PersistentAdbConnectionManager(
        _persistent_adb_target_provider(process_manager),
    )
    service = ControlSurfaceService(
        repository_root=args.repository_root,
        control_file=args.control_file,
        action_log=args.action_log,
        battles_dir=args.battles_dir,
        tournaments_dir=args.tournaments_dir,
        discarded_battles_dir=args.discarded_battles_dir,
        discarded_battle_retention_days=args.discarded_battle_retention_days,
        host_performance_db=args.host_performance_db,
        host_performance_retention_days=args.host_performance_retention_days,
        stale_after_seconds=args.stale_after_seconds,
        process_manager=process_manager,
        adb_connection_manager=adb_connection_manager,
        strategy_profile_dir=strategy_profile_dir,
        module_preset_dir=args.module_preset_directory,
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
    background_stop = threading.Event()
    retention_thread = threading.Thread(
        target=_discard_retention_loop,
        args=(service, background_stop),
        daemon=True,
        name="discard-retention",
    )
    adb_connection_thread = threading.Thread(
        target=adb_connection_manager.run,
        args=(background_stop,),
        daemon=True,
        name="persistent-adb-connection",
    )
    mapping_reconciliation_thread = threading.Thread(
        target=_save_mapping_reconciliation_loop,
        args=(service, background_stop),
        daemon=True,
        name="save-mapping-reconciliation",
    )
    retention_thread.start()
    adb_connection_thread.start()
    mapping_reconciliation_thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Stopping TheTower control surface.")
    finally:
        background_stop.set()
        retention_thread.join(timeout=2)
        adb_connection_thread.join(timeout=2)
        mapping_reconciliation_thread.join(timeout=2)
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
