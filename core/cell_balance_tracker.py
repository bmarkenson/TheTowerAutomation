"""Durable, observation-only tracking for the player's Elite Cell balance.

The tracker consumes normalized values from already-owned player-save
acquisitions.  It never reads a save, sends device input, or treats a balance
trend as authority to change Lab Speedups.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
)
from core.runtime_save import CellBalanceSnapshot, NormalizedRuntimeSave


CELL_BALANCE_STATUS_SCHEMA_VERSION = 1
CELL_BALANCE_STORE_SCHEMA_VERSION = 1
CELL_BALANCE_CAPABILITY_ID = "thetower.player_save.cell_balance.v1"
DEFAULT_CELL_BALANCE_RETENTION_DAYS = 90
DEFAULT_CELL_BALANCE_MAX_SAMPLES = 30_000
CELL_BALANCE_TREND_WINDOW_HOURS = 24
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class CellBalanceStorageError(RuntimeError):
    """The bounded Cell balance history could not be persisted or read."""


class CellBalanceTracker:
    """Persist comparable Cell balance checkpoints and summarize their trend."""

    def __init__(
        self,
        path: Path | str,
        *,
        buffer_floor: Optional[int] = None,
        retention_days: int = DEFAULT_CELL_BALANCE_RETENTION_DAYS,
        max_samples: int = DEFAULT_CELL_BALANCE_MAX_SAMPLES,
    ) -> None:
        self.path = Path(path)
        if type(retention_days) is not int or retention_days < 1:
            raise ValueError("Cell balance retention must be positive")
        if type(max_samples) is not int or max_samples < 2:
            raise ValueError("Cell balance sample capacity must be at least two")
        self.buffer_floor = _buffer_floor_decimal(buffer_floor)
        self.retention_days = retention_days
        self.max_samples = max_samples
        self._lock = threading.RLock()
        self._storage_reason = ""

    def set_buffer_floor(self, value: object) -> bool:
        """Apply a live planner reserve without restarting observation."""

        floor = _buffer_floor_decimal(value)
        with self._lock:
            changed = floor != self.buffer_floor
            self.buffer_floor = floor
        return changed

    def observe_bundle(self, acquisition: PlayerSaveAcquisitionBundle) -> str:
        """Persist one normalized balance from an existing typed acquisition."""

        if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
            return "rejected_typed_acquisition_required"
        if (
            acquisition.status is not PlayerSaveAcquisitionStatus.COMPLETE
            or not acquisition.complete
            or acquisition.snapshot is None
            or acquisition.binding is None
        ):
            return "rejected_acquisition_unavailable"

        snapshot = acquisition.snapshot
        runtime = getattr(snapshot, "runtime_save", None)
        balance = getattr(runtime, "cell_balance", None)
        capability = getattr(snapshot, "capability", lambda _value: None)(
            CELL_BALANCE_CAPABILITY_ID
        )
        if not (
            isinstance(runtime, NormalizedRuntimeSave)
            and isinstance(balance, CellBalanceSnapshot)
            and balance.status == "observed"
            and balance.capability_id == CELL_BALANCE_CAPABILITY_ID
            and balance.evidence_level == "structural_observation"
            and balance.forward_policy == "exact_version_only"
            and capability is not None
            and getattr(capability, "status", None) == "observed"
            and getattr(capability, "semantic_fingerprint", None)
            == balance.semantic_fingerprint
            and getattr(capability, "binding_fingerprint", None)
            == balance.binding_fingerprint
        ):
            return "rejected_cell_balance_capability_unavailable"

        try:
            value = _nonnegative_decimal(balance.value_decimal)
            captured_at = _aware_utc(acquisition.captured_at)
            source_fingerprint = str(
                getattr(snapshot, "source_sha256", "") or ""
            ).lower()
            target_fingerprint = hashlib.sha256(
                (
                    "thetower-cell-balance-target-v1\0"
                    f"{acquisition.binding.target}"
                ).encode("utf-8")
            ).hexdigest()
            semantic_fingerprint = str(balance.semantic_fingerprint).lower()
            binding_fingerprint = str(balance.binding_fingerprint).lower()
            mapping_id = str(getattr(snapshot, "mapping_id", "") or "")
            game_version = getattr(snapshot, "game_version", None)
            save_revision = getattr(runtime, "save_revision", None)
            if type(save_revision) is not int or save_revision < 0:
                save_revision = None
            if (
                _SHA256_RE.fullmatch(source_fingerprint) is None
                or _SHA256_RE.fullmatch(target_fingerprint) is None
                or _SHA256_RE.fullmatch(semantic_fingerprint) is None
                or _SHA256_RE.fullmatch(binding_fingerprint) is None
                or not mapping_id
                or type(game_version) is not int
            ):
                raise ValueError("cell_balance_provenance_invalid")
        except (InvalidOperation, TypeError, ValueError):
            return "rejected_cell_balance_projection_invalid"

        observation_id = hashlib.sha256(
            (
                "thetower-cell-balance-observation-v1\0"
                f"{source_fingerprint}\0{captured_at.isoformat()}\0"
                f"{target_fingerprint}\0"
                f"{semantic_fingerprint}\0{binding_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        row = {
            "observation_id": observation_id,
            "captured_at": captured_at.isoformat(),
            "captured_unix": captured_at.timestamp(),
            "balance_decimal": _decimal_text(value),
            "source_fingerprint": source_fingerprint,
            "target_fingerprint": target_fingerprint,
            "acquisition_type": acquisition.acquisition_type.value,
            "mapping_id": mapping_id,
            "game_version": game_version,
            "save_revision": save_revision,
            "semantic_fingerprint": semantic_fingerprint,
            "binding_fingerprint": binding_fingerprint,
            "evidence_level": balance.evidence_level,
        }
        with self._lock:
            try:
                with self._connection() as connection:
                    latest = self._latest_comparable(
                        connection,
                        target_fingerprint=target_fingerprint,
                        semantic_fingerprint=semantic_fingerprint,
                        binding_fingerprint=binding_fingerprint,
                    )
                    if (
                        latest is not None
                        and float(latest["captured_unix"])
                        > row["captured_unix"]
                    ):
                        return "ignored_out_of_order_observation"
                    row["continuity_id"] = _continuity_id(
                        latest,
                        observation_id=observation_id,
                        save_revision=save_revision,
                    )
                    inserted = connection.execute(
                        """
                        INSERT OR IGNORE INTO cell_balance_observations (
                            observation_id,
                            captured_at,
                            captured_unix,
                            balance_decimal,
                            source_fingerprint,
                            target_fingerprint,
                            acquisition_type,
                            mapping_id,
                            game_version,
                            save_revision,
                            semantic_fingerprint,
                            binding_fingerprint,
                            evidence_level,
                            continuity_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        tuple(
                            row[field]
                            for field in (
                                "observation_id",
                                "captured_at",
                                "captured_unix",
                                "balance_decimal",
                                "source_fingerprint",
                                "target_fingerprint",
                                "acquisition_type",
                                "mapping_id",
                                "game_version",
                                "save_revision",
                                "semantic_fingerprint",
                                "binding_fingerprint",
                                "evidence_level",
                                "continuity_id",
                            )
                        ),
                    ).rowcount
                    if inserted:
                        self._prune(connection, newest_at=row["captured_unix"])
                    self._storage_reason = ""
                    return "accepted_observation" if inserted else "ignored_duplicate"
            except (OSError, sqlite3.Error, CellBalanceStorageError) as exc:
                self._storage_reason = _safe_reason(exc)
                return "rejected_storage_unavailable"

    def status(self) -> dict[str, Any]:
        """Return the latest persisted balance and comparable net trend."""

        with self._lock:
            try:
                if not self.path.exists():
                    return self._unavailable("cell_balance_history_empty")
                with self._connection(create=False) as connection:
                    latest = self._latest(connection)
                    if latest is None:
                        return self._unavailable("cell_balance_history_empty")
                    comparable = self._comparable_rows(connection, latest)
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM cell_balance_observations"
                        ).fetchone()[0]
                    )
                self._storage_reason = ""
            except (OSError, sqlite3.Error, CellBalanceStorageError) as exc:
                self._storage_reason = _safe_reason(exc)
                return self._unavailable("cell_balance_storage_unavailable")

        try:
            balance = _nonnegative_decimal(latest["balance_decimal"])
            prior = comparable[-2] if len(comparable) >= 2 else None
            previous_change = _change_summary(prior, latest) if prior else None
            window_baseline, window_basis = _window_baseline(
                comparable,
                latest,
            )
            window_change = (
                _change_summary(window_baseline, latest)
                if window_baseline is not None
                else None
            )
            direction = (
                _direction(_decimal(window_change["change_decimal"]))
                if window_change is not None
                else "unknown"
            )
            buffer = self._buffer_status(balance, window_change)
        except (InvalidOperation, TypeError, ValueError):
            return self._unavailable("cell_balance_history_invalid")
        return {
            "schema_version": CELL_BALANCE_STATUS_SCHEMA_VERSION,
            "status": "observed",
            "reason": "",
            "captured_at": latest["captured_at"],
            "balance_decimal": _decimal_text(balance),
            "unit": "cells",
            "trend": {
                "direction": direction,
                "basis": window_basis,
                "change_decimal": (
                    window_change["change_decimal"]
                    if window_change is not None
                    else None
                ),
                "elapsed_hours_decimal": (
                    window_change["elapsed_hours_decimal"]
                    if window_change is not None
                    else None
                ),
                "net_per_hour_decimal": (
                    window_change["net_per_hour_decimal"]
                    if window_change is not None
                    else None
                ),
            },
            "previous": previous_change,
            "buffer": buffer,
            "history": {
                "sample_count": count,
                "comparable_sample_count": len(comparable),
                "retention_days": self.retention_days,
                "max_samples": self.max_samples,
            },
            "provenance": {
                "acquisition_type": latest["acquisition_type"],
                "mapping_id": latest["mapping_id"],
                "game_version": latest["game_version"],
                "target_fingerprint": latest["target_fingerprint"],
                "semantic_fingerprint": latest["semantic_fingerprint"],
                "binding_fingerprint": latest["binding_fingerprint"],
                "evidence_level": latest["evidence_level"],
                "save_revision": latest["save_revision"],
            },
            "ui_action_authority": False,
        }

    def _buffer_status(
        self,
        balance: Decimal,
        window_change: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            floor = self.buffer_floor
        if floor is None:
            return {
                "status": "not_configured",
                "floor_decimal": None,
                "headroom_decimal": None,
                "estimated_hours_to_floor_decimal": None,
                "automatic_reduction_enabled": False,
            }
        headroom = balance - floor
        estimate: Optional[Decimal] = None
        if headroom > 0 and window_change is not None:
            rate = _decimal(window_change["net_per_hour_decimal"])
            if rate < 0:
                with localcontext() as context:
                    context.prec = 50
                    estimate = headroom / -rate
        return {
            "status": "below" if headroom < 0 else "at" if headroom == 0 else "above",
            "floor_decimal": _decimal_text(floor),
            "headroom_decimal": _signed_decimal_text(headroom),
            "estimated_hours_to_floor_decimal": (
                _decimal_text(estimate) if estimate is not None else None
            ),
            "automatic_reduction_enabled": False,
        }

    def _connect(self, *, create: bool = True) -> sqlite3.Connection:
        if not create and not self.path.exists():
            raise CellBalanceStorageError("cell_balance_history_empty")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                if not create:
                    raise CellBalanceStorageError(
                        "cell_balance_store_schema_unavailable"
                    )
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS cell_balance_observations (
                        observation_id TEXT PRIMARY KEY,
                        captured_at TEXT NOT NULL,
                        captured_unix REAL NOT NULL,
                        balance_decimal TEXT NOT NULL,
                        source_fingerprint TEXT NOT NULL,
                        target_fingerprint TEXT NOT NULL,
                        acquisition_type TEXT NOT NULL,
                        mapping_id TEXT NOT NULL,
                        game_version INTEGER NOT NULL,
                        save_revision INTEGER,
                        semantic_fingerprint TEXT NOT NULL,
                        binding_fingerprint TEXT NOT NULL,
                        evidence_level TEXT NOT NULL,
                        continuity_id TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS cell_balance_captured_idx
                    ON cell_balance_observations(captured_unix);
                    CREATE INDEX IF NOT EXISTS cell_balance_comparable_idx
                    ON cell_balance_observations(
                        target_fingerprint,
                        semantic_fingerprint,
                        binding_fingerprint,
                        continuity_id,
                        captured_unix
                    );
                    PRAGMA user_version = 1;
                    """
                )
                connection.commit()
            elif version != CELL_BALANCE_STORE_SCHEMA_VERSION:
                raise CellBalanceStorageError(
                    "cell_balance_store_schema_changed"
                )
            if create and not existed:
                self.path.chmod(0o600)
            return connection
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _connection(
        self,
        *,
        create: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect(create=create)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _latest(connection: sqlite3.Connection) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM cell_balance_observations
            ORDER BY captured_unix DESC, observation_id DESC
            LIMIT 1
            """
        ).fetchone()

    @staticmethod
    def _latest_comparable(
        connection: sqlite3.Connection,
        *,
        target_fingerprint: str,
        semantic_fingerprint: str,
        binding_fingerprint: str,
    ) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM cell_balance_observations
            WHERE target_fingerprint = ?
              AND semantic_fingerprint = ?
              AND binding_fingerprint = ?
            ORDER BY captured_unix DESC, observation_id DESC
            LIMIT 1
            """,
            (
                target_fingerprint,
                semantic_fingerprint,
                binding_fingerprint,
            ),
        ).fetchone()

    @staticmethod
    def _comparable_rows(
        connection: sqlite3.Connection,
        latest: sqlite3.Row,
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT * FROM cell_balance_observations
                WHERE target_fingerprint = ?
                  AND semantic_fingerprint = ?
                  AND binding_fingerprint = ?
                  AND continuity_id = ?
                  AND captured_unix <= ?
                ORDER BY captured_unix ASC, observation_id ASC
                """,
                (
                    latest["target_fingerprint"],
                    latest["semantic_fingerprint"],
                    latest["binding_fingerprint"],
                    latest["continuity_id"],
                    latest["captured_unix"],
                ),
            )
        )

    def _prune(self, connection: sqlite3.Connection, *, newest_at: float) -> None:
        cutoff = newest_at - self.retention_days * 24 * 60 * 60
        connection.execute(
            "DELETE FROM cell_balance_observations WHERE captured_unix < ?",
            (cutoff,),
        )
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM cell_balance_observations"
            ).fetchone()[0]
        )
        excess = count - self.max_samples
        if excess > 0:
            connection.execute(
                """
                DELETE FROM cell_balance_observations
                WHERE observation_id IN (
                    SELECT observation_id
                    FROM cell_balance_observations
                    ORDER BY captured_unix ASC, observation_id ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )

    def _unavailable(self, reason: str) -> dict[str, Any]:
        return {
            "schema_version": CELL_BALANCE_STATUS_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": reason,
            "captured_at": None,
            "balance_decimal": None,
            "unit": "cells",
            "trend": None,
            "previous": None,
            "buffer": {
                "status": (
                    "not_configured"
                    if self.buffer_floor is None
                    else "unavailable"
                ),
                "floor_decimal": (
                    _decimal_text(self.buffer_floor)
                    if self.buffer_floor is not None
                    else None
                ),
                "headroom_decimal": None,
                "estimated_hours_to_floor_decimal": None,
                "automatic_reduction_enabled": False,
            },
            "history": {
                "sample_count": 0,
                "comparable_sample_count": 0,
                "retention_days": self.retention_days,
                "max_samples": self.max_samples,
            },
            "provenance": None,
            "ui_action_authority": False,
        }


def _window_baseline(
    rows: list[sqlite3.Row],
    latest: sqlite3.Row,
) -> tuple[Optional[sqlite3.Row], str]:
    if len(rows) < 2:
        return None, "insufficient_history"
    target = float(latest["captured_unix"]) - (
        CELL_BALANCE_TREND_WINDOW_HOURS * 60 * 60
    )
    older = [row for row in rows[:-1] if float(row["captured_unix"]) <= target]
    if older:
        return older[-1], "24h_window"
    return rows[0], "since_comparable_start"


def _continuity_id(
    latest: Optional[sqlite3.Row],
    *,
    observation_id: str,
    save_revision: Optional[int],
) -> str:
    """Start a new comparison epoch when the save revision moves backward."""

    if latest is None:
        return observation_id
    prior_revision = latest["save_revision"]
    if (
        type(save_revision) is int
        and type(prior_revision) is int
        and save_revision < prior_revision
    ):
        return observation_id
    continuity_id = str(latest["continuity_id"] or "")
    return continuity_id or observation_id


def _change_summary(
    baseline: sqlite3.Row,
    latest: sqlite3.Row,
) -> dict[str, Any]:
    change = _decimal(latest["balance_decimal"]) - _decimal(
        baseline["balance_decimal"]
    )
    elapsed_seconds = max(
        Decimal("0"),
        Decimal(str(latest["captured_unix"]))
        - Decimal(str(baseline["captured_unix"])),
    )
    with localcontext() as context:
        context.prec = 50
        elapsed_hours = elapsed_seconds / Decimal(3600)
        rate = change / elapsed_hours if elapsed_hours > 0 else Decimal(0)
    return {
        "baseline_captured_at": baseline["captured_at"],
        "change_decimal": _signed_decimal_text(change),
        "elapsed_hours_decimal": _decimal_text(elapsed_hours),
        "net_per_hour_decimal": _signed_decimal_text(rate),
    }


def _direction(value: Decimal) -> str:
    return "rising" if value > 0 else "falling" if value < 0 else "flat"


def _nonnegative_decimal(value: Any) -> Decimal:
    number = _decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("cell_balance_value_invalid")
    return number


def _buffer_floor_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    number = _nonnegative_decimal(value)
    if number != number.to_integral_value():
        raise ValueError("Cell buffer floor must be a nonnegative integer")
    return number


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("cell_balance_decimal_invalid")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("cell_balance_decimal_invalid")
    return number


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _signed_decimal_text(value: Decimal) -> str:
    text = _decimal_text(abs(value))
    return f"-{text}" if value < 0 else text


def _aware_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("cell_balance_capture_time_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_reason(exc: BaseException) -> str:
    reason = re.sub(r"[^a-z0-9]+", "_", str(exc).strip().lower()).strip("_")
    return reason[:160] or exc.__class__.__name__.lower()


__all__ = [
    "CELL_BALANCE_CAPABILITY_ID",
    "CELL_BALANCE_STATUS_SCHEMA_VERSION",
    "CellBalanceStorageError",
    "CellBalanceTracker",
    "DEFAULT_CELL_BALANCE_MAX_SAMPLES",
    "DEFAULT_CELL_BALANCE_RETENTION_DAYS",
]
