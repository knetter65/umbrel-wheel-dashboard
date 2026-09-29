"""SQLite persistence boundary for sanitized Wheel dashboard snapshots.

Only normalized, account-identifier-free data crosses this boundary. Phase 1
bootstraps a local database from synthetic fixtures; later adapters can target
the same tables without changing the Dash presentation layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .fixtures import ROOT, load_snapshot, synthetic_bars
from .indicators import bollinger_bands, rsi, sma

SCHEMA_PATH = ROOT / "schema" / "wheel_dashboard.sql"
FIXTURE_PATH = ROOT / "fixtures" / "dashboard_snapshot.json"
DATA_ROOT = Path(os.environ.get("WHEEL_DASHBOARD_DATA_DIR", ROOT / "data"))
ACTIVE_DB_PATH = DATA_ROOT / "active-readonly.db"
_DB_OVERRIDE = os.environ.get("WHEEL_DASHBOARD_DB")
_READ_ONLY_RUNTIME = os.environ.get("WHEEL_DASHBOARD_READ_ONLY", "").lower() in {"1", "true", "yes"}
FIXTURE_DB_PATH = (ROOT / "fixtures" / "phase1-fixture.db") if _READ_ONLY_RUNTIME else (DATA_ROOT / "phase1-fixture.db")
DEFAULT_DB_PATH = Path(
    _DB_OVERRIDE
    or (ACTIVE_DB_PATH if ACTIVE_DB_PATH.is_file() else FIXTURE_DB_PATH)
)
MICRO = Decimal("1000000")
SCHEMA_VERSION = "2"


def to_micros(value: Any | None) -> int | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * MICRO).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_micros(value: int | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(value) / MICRO, "f")


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    if _READ_ONLY_RUNTIME:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database(db_path: Path | str = DEFAULT_DB_PATH):
    """Commit or roll back as appropriate and always close the connection."""
    connection = connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with database(path) as connection:
        connection.executescript(SCHEMA_PATH.read_text())
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            raise RuntimeError(f"SQLite WAL mode was not enabled: {mode}")


def _fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def _secure_sqlite_files(path: Path) -> None:
    if _READ_ONLY_RUNTIME:
        return
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.chmod(0o600)


def _validate_multisource_database(path: Path, label: str) -> Path:
    """Validate an explicit staging or promoted read-only dashboard source."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{label} dashboard database does not exist: {path}")
    with database(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        kinds = {row[0] for row in connection.execute("SELECT source_kind FROM source_snapshot")}
        hard_stops = connection.execute(
            "SELECT COUNT(*) FROM reconciliation_issue WHERE severity='HARD_STOP'"
        ).fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"{label} dashboard database failed integrity check: {integrity}")
    if foreign_keys:
        raise RuntimeError(f"{label} dashboard database has foreign-key violations")
    required = {"BROKER_HISTORY", "BROKER_SNAPSHOT", "MARKET_HISTORY"}
    if not required.issubset(kinds):
        raise RuntimeError(f"{label} dashboard database lacks required sources: {sorted(required - kinds)}")
    if hard_stops:
        raise RuntimeError(f"{label} dashboard database contains {hard_stops} HARD_STOP issue(s)")
    _secure_sqlite_files(path)
    return path


def resolve_dashboard_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Resolve fixture fallback, explicit staging override, or promoted active DB."""
    path = Path(db_path)
    if _DB_OVERRIDE:
        return _validate_multisource_database(path, "explicit staging")
    if path == ACTIVE_DB_PATH:
        return _validate_multisource_database(path, "promoted active")
    return ensure_fixture_database(path)


def ensure_fixture_database(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Build or refresh a fixture-only database, refusing to erase non-fixture data."""
    path = Path(db_path)
    expected_hash = _fixture_sha256()
    if path.exists():
        try:
            with database(path) as connection:
                rows = connection.execute(
                    "SELECT source_kind, content_sha256 FROM source_snapshot"
                ).fetchall()
                schema_row = connection.execute(
                    "SELECT value FROM app_metadata WHERE key='schema_version'"
                ).fetchone()
                schema_version = schema_row[0] if schema_row else ""
            if rows and all(row["source_kind"] == "FIXTURE" for row in rows):
                if schema_version == SCHEMA_VERSION and any(row["content_sha256"] == expected_hash for row in rows):
                    _secure_sqlite_files(path)
                    return path
            elif rows:
                raise RuntimeError("refusing to replace a database containing non-fixture sources")
        except sqlite3.DatabaseError:
            # A malformed development fixture DB may be recreated; no broker data
            # is permitted at this path in Phase 1.
            pass
        _remove_sqlite_files(path)

    initialize_database(path)
    import_snapshot(path, load_snapshot(), content_sha256=expected_hash)
    _secure_sqlite_files(path)
    return path


def import_snapshot(
    db_path: Path | str,
    snapshot: dict[str, Any],
    *,
    content_sha256: str,
) -> None:
    if snapshot.get("namespace") != "wheel-dashboard":
        raise ValueError("only the wheel-dashboard namespace is accepted")
    source = snapshot["sources"][0]
    if source["kind"] != "FIXTURE":
        raise ValueError("Phase-1 importer accepts FIXTURE sources only")
    snapshot_id = snapshot["snapshot_id"]
    generated_at = snapshot["generated_at"]

    with database(db_path) as connection:
        connection.execute(
            """
            INSERT INTO source_snapshot
              (snapshot_id, source_kind, quality, generated_at, cutoff_at,
               imported_at, source_reference, warnings_json, content_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
              source_kind=excluded.source_kind,
              quality=excluded.quality,
              generated_at=excluded.generated_at,
              cutoff_at=excluded.cutoff_at,
              imported_at=excluded.imported_at,
              source_reference=excluded.source_reference,
              warnings_json=excluded.warnings_json,
              content_sha256=excluded.content_sha256
            """,
            (
                snapshot_id,
                source["kind"],
                source["quality"],
                generated_at,
                source["cutoff_at"],
                generated_at,
                "fixtures/dashboard_snapshot.json",
                json.dumps(source.get("warnings", []), ensure_ascii=False),
                content_sha256,
            ),
        )

        cycle_by_symbol = {cycle["underlying"]: cycle for cycle in snapshot["cycles"]}
        symbols = sorted(
            {item["underlying"] for item in snapshot["positions"]}
            | set(cycle_by_symbol)
        )
        underlying_ids: dict[str, int] = {}
        for symbol in symbols:
            thesis = cycle_by_symbol.get(symbol, {}).get("thesis_state", "REVIEW_DUE")
            connection.execute(
                """
                INSERT INTO underlying(symbol, currency, approval_status, thesis_state)
                VALUES (?, 'USD', 'UNREVIEWED', ?)
                ON CONFLICT(symbol) DO UPDATE SET thesis_state=excluded.thesis_state
                """,
                (symbol, thesis),
            )
            underlying_ids[symbol] = connection.execute(
                "SELECT underlying_id FROM underlying WHERE symbol=?", (symbol,)
            ).fetchone()[0]

        closed_reasons = {
            "CLOSED_PUT_ONLY": "PUT_ONLY",
            "CLOSED_CALLED_AWAY": "CALLED_AWAY",
            "CLOSED_STOCK_SOLD": "STOCK_SOLD",
        }
        leg_by_occ: dict[str, str] = {}
        for cycle in snapshot["cycles"]:
            connection.execute(
                """
                INSERT INTO wheel_cycle
                  (cycle_id, underlying_id, opened_at, closed_at,
                   original_put_strike_micros, original_exit_target_micros,
                   lifecycle_state, confidence, closed_reason, notes,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                  underlying_id=excluded.underlying_id,
                  opened_at=excluded.opened_at,
                  closed_at=excluded.closed_at,
                  original_put_strike_micros=excluded.original_put_strike_micros,
                  original_exit_target_micros=excluded.original_exit_target_micros,
                  lifecycle_state=excluded.lifecycle_state,
                  confidence=excluded.confidence,
                  closed_reason=excluded.closed_reason,
                  updated_at=excluded.updated_at
                """,
                (
                    cycle["cycle_id"],
                    underlying_ids[cycle["underlying"]],
                    cycle["opened_at"],
                    cycle.get("closed_at"),
                    to_micros(cycle["original_put_strike"]),
                    to_micros(cycle["original_put_strike"]),
                    cycle["state"],
                    cycle["confidence"],
                    closed_reasons.get(cycle["state"]),
                    generated_at,
                    generated_at,
                ),
            )
            for leg in cycle["option_legs"]:
                connection.execute(
                    """
                    INSERT INTO option_leg
                      (option_leg_id, cycle_id, parent_leg_id, underlying_id,
                       occ_symbol, side, strike_micros, expiration, multiplier,
                       contracts, opened_at, terminated_at, termination_kind,
                       leg_state, source_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(option_leg_id) DO UPDATE SET
                      parent_leg_id=excluded.parent_leg_id,
                      occ_symbol=excluded.occ_symbol,
                      strike_micros=excluded.strike_micros,
                      expiration=excluded.expiration,
                      multiplier=excluded.multiplier,
                      contracts=excluded.contracts,
                      leg_state=excluded.leg_state,
                      source_confidence=excluded.source_confidence
                    """,
                    (
                        leg["option_leg_id"],
                        cycle["cycle_id"],
                        leg.get("parent_leg_id"),
                        underlying_ids[cycle["underlying"]],
                        leg["occ_symbol"],
                        leg["side"],
                        to_micros(leg["strike"]),
                        leg["expiration"],
                        leg.get("multiplier", 100),
                        leg["contracts"],
                        cycle["opened_at"],
                        cycle.get("closed_at") if leg["state"] != "OPEN" else None,
                        leg["state"] if leg["state"] in {"CLOSED", "EXPIRED", "ASSIGNED", "EXERCISED"} else None,
                        leg["state"],
                        cycle["confidence"],
                    ),
                )
                leg_by_occ[leg["occ_symbol"]] = leg["option_leg_id"]

        connection.execute("DELETE FROM current_position WHERE snapshot_id=?", (snapshot_id,))
        for position in snapshot["positions"]:
            connection.execute(
                """
                INSERT INTO current_position
                  (position_id, snapshot_id, cycle_id, underlying_id, option_leg_id,
                   asset_kind, quantity_micros, market_price_micros,
                   broker_cost_basis_micros, market_value_micros,
                   unrealized_pnl_micros)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position["position_id"],
                    snapshot_id,
                    position.get("cycle_id"),
                    underlying_ids[position["underlying"]],
                    leg_by_occ.get(position.get("occ_symbol")),
                    position["asset_kind"],
                    to_micros(position["quantity"]),
                    to_micros(position.get("market_price")),
                    None,
                    to_micros(position.get("market_value")),
                    to_micros(position.get("unrealized_pnl")),
                ),
            )

        account = snapshot["account"]
        connection.execute(
            """
            INSERT OR REPLACE INTO account_metric_snapshot
              (snapshot_id, currency, total_cash_micros, net_liquidation_micros,
               external_net_contributions_micros,
               open_put_assignment_obligation_micros,
               cash_after_all_assignments_micros, realized_trading_pnl_micros,
               unrealized_trading_pnl_micros)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                account["currency"],
                to_micros(account.get("total_cash")),
                to_micros(account.get("net_liquidation")),
                to_micros(account["external_net_contributions"]),
                to_micros(account["open_put_assignment_obligation"]),
                to_micros(account.get("cash_after_all_assignments")),
                to_micros(account.get("realized_trading_pnl")),
                to_micros(account.get("unrealized_trading_pnl")),
            ),
        )

        connection.execute("DELETE FROM cycle_metric_snapshot WHERE snapshot_id=?", (snapshot_id,))
        for cycle in snapshot["cycles"]:
            connection.execute(
                """
                INSERT INTO cycle_metric_snapshot
                  (cycle_id, snapshot_id, remaining_shares_micros,
                   stock_net_cashflow_micros, realized_put_pnl_micros,
                   realized_call_pnl_micros, open_option_cashflow_micros,
                   open_option_close_cost_micros, dividends_net_micros,
                   broker_basis_per_share_micros, locked_break_even_micros,
                   locked_break_even_with_dividends_micros,
                   cashflow_break_even_micros, cashflow_put_break_even_micros,
                   liquidation_break_even_micros, total_cycle_mtm_pnl_micros,
                   calculation_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'phase1-v1')
                """,
                (
                    cycle["cycle_id"],
                    snapshot_id,
                    to_micros(cycle["remaining_shares"]),
                    to_micros(cycle["stock_net_cashflow"]),
                    to_micros(cycle["realized_put_pnl"]),
                    to_micros(cycle["realized_call_pnl"]),
                    to_micros(cycle["open_option_cashflow"]),
                    to_micros(cycle["open_option_close_cost"]),
                    to_micros(cycle["dividends_net"]),
                    to_micros(cycle.get("broker_basis_per_share")),
                    to_micros(cycle.get("locked_break_even")),
                    to_micros(cycle.get("locked_break_even_with_dividends")),
                    to_micros(cycle.get("cashflow_break_even")),
                    to_micros(cycle.get("cashflow_put_break_even")),
                    to_micros(cycle.get("liquidation_break_even")),
                ),
            )

        connection.execute("DELETE FROM market_bar WHERE snapshot_id=?", (snapshot_id,))
        connection.execute("DELETE FROM technical_snapshot WHERE snapshot_id=?", (snapshot_id,))
        for symbol in symbols:
            bars = synthetic_bars(symbol)
            closes = [bar["close"] for bar in bars]
            lower, middle, upper = bollinger_bands(closes)
            sma20, sma50, sma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
            rsi14 = rsi(closes, 14)
            for bar in bars:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO market_bar
                      (underlying_id, session_date, interval, open_micros,
                       high_micros, low_micros, close_micros, volume, snapshot_id)
                    VALUES (?, ?, '1d', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        underlying_ids[symbol],
                        bar["date"],
                        to_micros(bar["open"]),
                        to_micros(bar["high"]),
                        to_micros(bar["low"]),
                        to_micros(bar["close"]),
                        bar["volume"],
                        snapshot_id,
                    ),
                )
            idx = len(closes) - 1
            width = (upper[idx] or closes[idx]) - (lower[idx] or closes[idx])
            bb_position = 0.5 if width == 0 else (closes[idx] - (lower[idx] or closes[idx])) / width
            current_rsi = rsi14[idx]
            rating = "ACCEPTABLE"
            if current_rsi is not None and current_rsi < 30:
                rating = "CAUTION"
            if sma50[idx] is not None and closes[idx] < sma50[idx] and current_rsi is not None and current_rsi < 35:
                rating = "REJECT"
            stabilization = int(closes[idx] >= min(closes[idx - 3 : idx]))

            def ret(period: int) -> int | None:
                if len(closes) <= period:
                    return None
                return to_micros((Decimal(str(closes[-1])) / Decimal(str(closes[-1 - period]))) - 1)

            connection.execute(
                """
                INSERT INTO technical_snapshot
                  (underlying_id, snapshot_id, as_of_session, close_micros,
                   sma20_micros, sma50_micros, sma200_micros,
                   bollinger_period, bollinger_stddev_micros,
                   bollinger_lower_micros, bollinger_middle_micros,
                   bollinger_upper_micros, bollinger_position_millionths,
                   rsi_period, rsi_millionths, return_5d_millionths,
                   return_20d_millionths, return_60d_millionths,
                   return_1y_millionths, stabilization, rating)
                VALUES (?, ?, ?, ?, ?, ?, ?, 20, 2000000, ?, ?, ?, ?, 14, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    underlying_ids[symbol],
                    snapshot_id,
                    bars[-1]["date"],
                    to_micros(closes[idx]),
                    to_micros(sma20[idx]),
                    to_micros(sma50[idx]),
                    to_micros(sma200[idx]),
                    to_micros(lower[idx]),
                    to_micros(middle[idx]),
                    to_micros(upper[idx]),
                    to_micros(bb_position),
                    to_micros(current_rsi),
                    ret(5),
                    ret(20),
                    ret(60),
                    ret(252),
                    stabilization,
                    rating,
                ),
            )


def read_dashboard_snapshot(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    with database(db_path) as connection:
        sources = connection.execute(
            "SELECT * FROM source_snapshot ORDER BY generated_at DESC, source_kind"
        ).fetchall()
        if not sources:
            raise RuntimeError("dashboard database has no source snapshot")

        def authority_snapshot(table: str, priorities: tuple[str, ...]) -> sqlite3.Row:
            placeholders = ", ".join("?" for _ in priorities)
            order = " ".join(
                f"WHEN '{kind}' THEN {index}" for index, kind in enumerate(priorities)
            )
            row = connection.execute(
                f"""
                SELECT s.* FROM source_snapshot s
                JOIN {table} value ON value.snapshot_id=s.snapshot_id
                WHERE s.source_kind IN ({placeholders})
                ORDER BY CASE s.source_kind {order} ELSE 99 END,
                         s.generated_at DESC
                LIMIT 1
                """,
                priorities,
            ).fetchone()
            if row is None:
                raise RuntimeError(f"dashboard database has no authority for {table}")
            return row

        account_source = authority_snapshot(
            "account_metric_snapshot", ("BROKER_SNAPSHOT", "FIXTURE", "BROKER_HISTORY")
        )
        cycle_source = authority_snapshot(
            "cycle_metric_snapshot", ("BROKER_HISTORY", "FIXTURE")
        )
        technical_source = authority_snapshot(
            "technical_snapshot", ("MARKET_HISTORY", "FIXTURE")
        )
        snapshot_id = account_source["snapshot_id"]
        cycle_snapshot_id = cycle_source["snapshot_id"]
        technical_snapshot_id = technical_source["snapshot_id"]
        source = sources[0]
        account = connection.execute(
            "SELECT * FROM account_metric_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if account is None:
            raise RuntimeError("dashboard database has no account metrics")

        positions = []
        for row in connection.execute(
            """
            SELECT p.*, u.symbol, l.occ_symbol
            FROM current_position p
            JOIN underlying u ON u.underlying_id=p.underlying_id
            LEFT JOIN option_leg l ON l.option_leg_id=p.option_leg_id
            WHERE p.snapshot_id=? ORDER BY p.position_id
            """,
            (snapshot_id,),
        ):
            item = {
                "position_id": row["position_id"],
                "cycle_id": row["cycle_id"],
                "asset_kind": row["asset_kind"],
                "underlying": row["symbol"],
                "occ_symbol": row["occ_symbol"],
                "quantity": from_micros(row["quantity_micros"]),
                "currency": "USD",
            }
            for key, column in (
                ("market_price", "market_price_micros"),
                ("market_value", "market_value_micros"),
                ("unrealized_pnl", "unrealized_pnl_micros"),
            ):
                value = from_micros(row[column])
                if value is not None:
                    item[key] = value
            positions.append(item)

        cycles = []
        for row in connection.execute(
            """
            SELECT c.*, u.symbol, u.thesis_state, m.*
            FROM wheel_cycle c
            JOIN underlying u ON u.underlying_id=c.underlying_id
            JOIN cycle_metric_snapshot m ON m.cycle_id=c.cycle_id
            WHERE m.snapshot_id=? ORDER BY c.opened_at
            """,
            (cycle_snapshot_id,),
        ):
            cycle: dict[str, Any] = {
                "cycle_id": row["cycle_id"],
                "underlying": row["symbol"],
                "state": row["lifecycle_state"],
                "thesis_state": row["thesis_state"],
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "original_put_strike": from_micros(row["original_put_strike_micros"]),
                "remaining_shares": from_micros(row["remaining_shares_micros"]),
                "stock_net_cashflow": from_micros(row["stock_net_cashflow_micros"]),
                "realized_put_pnl": from_micros(row["realized_put_pnl_micros"]),
                "realized_call_pnl": from_micros(row["realized_call_pnl_micros"]),
                "open_option_cashflow": from_micros(row["open_option_cashflow_micros"]),
                "open_option_close_cost": from_micros(row["open_option_close_cost_micros"]),
                "dividends_net": from_micros(row["dividends_net_micros"]),
                "confidence": row["confidence"],
                "option_legs": [],
            }
            for key, column in (
                ("broker_basis_per_share", "broker_basis_per_share_micros"),
                ("locked_break_even", "locked_break_even_micros"),
                ("locked_break_even_with_dividends", "locked_break_even_with_dividends_micros"),
                ("cashflow_break_even", "cashflow_break_even_micros"),
                ("cashflow_put_break_even", "cashflow_put_break_even_micros"),
                ("liquidation_break_even", "liquidation_break_even_micros"),
            ):
                value = from_micros(row[column])
                if value is not None:
                    cycle[key] = value
            for leg in connection.execute(
                "SELECT * FROM option_leg WHERE cycle_id=? ORDER BY opened_at, option_leg_id",
                (row["cycle_id"],),
            ):
                cycle["option_legs"].append(
                    {
                        "option_leg_id": leg["option_leg_id"],
                        "parent_leg_id": leg["parent_leg_id"],
                        "occ_symbol": leg["occ_symbol"],
                        "side": leg["side"],
                        "strike": from_micros(leg["strike_micros"]),
                        "expiration": leg["expiration"],
                        "contracts": leg["contracts"],
                        "multiplier": leg["multiplier"],
                        "state": leg["leg_state"],
                    }
                )
            cycles.append(cycle)

        technicals = []
        for row in connection.execute(
            """
            SELECT t.*, u.symbol FROM technical_snapshot t
            JOIN underlying u ON u.underlying_id=t.underlying_id
            WHERE t.snapshot_id=? ORDER BY u.symbol
            """,
            (technical_snapshot_id,),
        ):
            item: dict[str, Any] = {
                "underlying": row["symbol"],
                "as_of_session": row["as_of_session"],
                "close": from_micros(row["close_micros"]),
                "bollinger_period": row["bollinger_period"],
                "bollinger_stddev": "2",
                "rsi_period": row["rsi_period"],
                "rating": row["rating"],
                "stabilization": bool(row["stabilization"]),
            }
            for key, column in (
                ("sma20", "sma20_micros"),
                ("sma50", "sma50_micros"),
                ("sma200", "sma200_micros"),
                ("bollinger_lower", "bollinger_lower_micros"),
                ("bollinger_middle", "bollinger_middle_micros"),
                ("bollinger_upper", "bollinger_upper_micros"),
                ("bollinger_position", "bollinger_position_millionths"),
                ("rsi", "rsi_millionths"),
            ):
                value = from_micros(row[column])
                if value is not None:
                    item[key] = value
            technicals.append(item)

        issues = [
            {
                "issue_id": row["issue_id"],
                "severity": row["severity"],
                "kind": row["issue_kind"],
                "cycle_id": row["cycle_id"],
                "message": row["message"],
            }
            for row in connection.execute(
                "SELECT * FROM reconciliation_issue ORDER BY severity, issue_id"
            )
        ]

        return {
            "schema_version": "1.0.0",
            "namespace": "wheel-dashboard",
            "snapshot_id": technical_snapshot_id,
            "generated_at": source["generated_at"],
            "sources": [
                {
                    "kind": item["source_kind"],
                    "quality": item["quality"],
                    "cutoff_at": item["cutoff_at"],
                    "content_sha256": item["content_sha256"],
                    "warnings": json.loads(item["warnings_json"]),
                }
                for item in sources
            ],
            "account": {
                "currency": account["currency"],
                "total_cash": from_micros(account["total_cash_micros"]),
                "net_liquidation": from_micros(account["net_liquidation_micros"]),
                "open_put_assignment_obligation": from_micros(account["open_put_assignment_obligation_micros"]),
                "cash_after_all_assignments": from_micros(account["cash_after_all_assignments_micros"]),
                "external_net_contributions": from_micros(account["external_net_contributions_micros"]),
                "realized_trading_pnl": from_micros(account["realized_trading_pnl_micros"]),
                "unrealized_trading_pnl": from_micros(account["unrealized_trading_pnl_micros"]),
            },
            "positions": positions,
            "cycles": cycles,
            "technicals": technicals,
            "reconciliation_issues": issues,
        }


def read_market_bars(db_path: Path | str, symbol: str) -> list[dict[str, Any]]:
    with database(db_path) as connection:
        rows = connection.execute(
            """
            SELECT b.* FROM market_bar b
            JOIN underlying u ON u.underlying_id=b.underlying_id
            WHERE u.symbol=? ORDER BY b.session_date
            """,
            (symbol,),
        ).fetchall()
    if not rows:
        raise KeyError(f"no market bars for {symbol}")
    return [
        {
            "date": row["session_date"],
            "open": float(Decimal(row["open_micros"]) / MICRO),
            "high": float(Decimal(row["high_micros"]) / MICRO),
            "low": float(Decimal(row["low_micros"]) / MICRO),
            "close": float(Decimal(row["close_micros"]) / MICRO),
            "volume": row["volume"],
        }
        for row in rows
    ]
