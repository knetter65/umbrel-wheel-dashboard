PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO app_metadata(key, value) VALUES ('schema_version', '2')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

-- Monetary and price fields use signed integer micro-units (1 USD = 1,000,000)
-- to avoid binary floating-point drift. Cash inflows are positive; outflows negative.

CREATE TABLE IF NOT EXISTS source_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('FIXTURE','BROKER_HISTORY','BROKER_SNAPSHOT','MARKET_HISTORY','OPTION_QUOTE','GATE_B','MANUAL')),
    quality TEXT NOT NULL CHECK (quality IN ('LIVE','DELAYED','STALE','MISSING')),
    generated_at TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    content_sha256 TEXT NOT NULL,
    CHECK (length(content_sha256) = 64)
);

CREATE TABLE IF NOT EXISTS underlying (
    underlying_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    currency TEXT NOT NULL DEFAULT 'USD',
    approval_status TEXT NOT NULL CHECK (approval_status IN ('CORE_APPROVED','SPECULATIVE_APPROVED','WATCH','REJECTED','INACTIVE','REVIEW_DUE','THESIS_BROKEN','UNREVIEWED')),
    approval_as_of TEXT,
    thesis_state TEXT NOT NULL DEFAULT 'INTACT' CHECK (thesis_state IN ('INTACT','WATCH','THESIS_BROKEN','REVIEW_DUE'))
);

CREATE TABLE IF NOT EXISTS wheel_cycle (
    cycle_id TEXT PRIMARY KEY,
    underlying_id INTEGER NOT NULL REFERENCES underlying(underlying_id),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    original_put_strike_micros INTEGER NOT NULL CHECK (original_put_strike_micros >= 0),
    original_exit_target_micros INTEGER NOT NULL CHECK (original_exit_target_micros >= 0),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('PUT_OPEN','ASSIGNED_NO_CALL','COVERED_CALL_OPEN','PARTIALLY_COVERED','CLOSED_PUT_ONLY','CLOSED_CALLED_AWAY','CLOSED_STOCK_SOLD','UNRESOLVED')),
    confidence TEXT NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW','MANUAL_REVIEW')),
    closed_reason TEXT CHECK (closed_reason IN ('PUT_ONLY','CALLED_AWAY','STOCK_SOLD')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((closed_at IS NULL AND closed_reason IS NULL) OR (closed_at IS NOT NULL AND closed_reason IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS option_leg (
    option_leg_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES wheel_cycle(cycle_id),
    parent_leg_id TEXT REFERENCES option_leg(option_leg_id),
    underlying_id INTEGER NOT NULL REFERENCES underlying(underlying_id),
    occ_symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('PUT','CALL')),
    strike_micros INTEGER NOT NULL CHECK (strike_micros >= 0),
    expiration TEXT NOT NULL,
    multiplier INTEGER NOT NULL DEFAULT 100 CHECK (multiplier > 0),
    contracts INTEGER NOT NULL CHECK (contracts > 0),
    opened_at TEXT NOT NULL,
    terminated_at TEXT,
    termination_kind TEXT CHECK (termination_kind IN ('CLOSED','EXPIRED','ASSIGNED','EXERCISED')),
    leg_state TEXT NOT NULL CHECK (leg_state IN ('OPEN','CLOSED','EXPIRED','ASSIGNED','EXERCISED','UNRESOLVED')),
    source_confidence TEXT NOT NULL CHECK (source_confidence IN ('HIGH','MEDIUM','LOW','MANUAL_REVIEW')),
    net_cash_micros INTEGER NOT NULL DEFAULT 0,
    UNIQUE (cycle_id, occ_symbol, opened_at)
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    ledger_id TEXT PRIMARY KEY,
    cycle_id TEXT REFERENCES wheel_cycle(cycle_id),
    option_leg_id TEXT REFERENCES option_leg(option_leg_id),
    occurred_at TEXT NOT NULL,
    entry_kind TEXT NOT NULL CHECK (entry_kind IN ('OPTION_TRADE','STOCK_TRADE','ASSIGNMENT','EXERCISE','EXPIRATION','COMMISSION','TAX','DIVIDEND','INTEREST','TRANSFER','MANUAL_ADJUSTMENT')),
    realization_state TEXT NOT NULL CHECK (realization_state IN ('REALIZED','OPEN_POSITION_CASHFLOW','NON_PNL','UNRESOLVED')),
    currency TEXT NOT NULL,
    amount_micros INTEGER NOT NULL,
    quantity_micros INTEGER,
    price_micros INTEGER,
    external_ref_hash TEXT,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
    description TEXT NOT NULL DEFAULT '',
    CHECK (entry_kind != 'TRANSFER' OR realization_state = 'NON_PNL')
);

CREATE UNIQUE INDEX IF NOT EXISTS cash_ledger_external_ref_unique
ON cash_ledger(external_ref_hash) WHERE external_ref_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS current_position (
    position_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
    cycle_id TEXT REFERENCES wheel_cycle(cycle_id),
    underlying_id INTEGER NOT NULL REFERENCES underlying(underlying_id),
    option_leg_id TEXT REFERENCES option_leg(option_leg_id),
    asset_kind TEXT NOT NULL CHECK (asset_kind IN ('STOCK','OPTION','CASH')),
    quantity_micros INTEGER NOT NULL,
    market_price_micros INTEGER,
    broker_cost_basis_micros INTEGER,
    market_value_micros INTEGER,
    unrealized_pnl_micros INTEGER,
    UNIQUE (snapshot_id, position_id)
);

CREATE TABLE IF NOT EXISTS cycle_metric_snapshot (
    cycle_id TEXT NOT NULL REFERENCES wheel_cycle(cycle_id),
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
    remaining_shares_micros INTEGER NOT NULL CHECK (remaining_shares_micros >= 0),
    stock_net_cashflow_micros INTEGER NOT NULL,
    realized_put_pnl_micros INTEGER NOT NULL,
    realized_call_pnl_micros INTEGER NOT NULL,
    open_option_cashflow_micros INTEGER NOT NULL,
    open_option_close_cost_micros INTEGER,
    dividends_net_micros INTEGER NOT NULL DEFAULT 0,
    broker_basis_per_share_micros INTEGER,
    locked_break_even_micros INTEGER,
    locked_break_even_with_dividends_micros INTEGER,
    cashflow_break_even_micros INTEGER,
    cashflow_put_break_even_micros INTEGER,
    liquidation_break_even_micros INTEGER,
    total_cycle_mtm_pnl_micros INTEGER,
    calculation_version TEXT NOT NULL,
    PRIMARY KEY (cycle_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS account_metric_snapshot (
    snapshot_id TEXT PRIMARY KEY REFERENCES source_snapshot(snapshot_id),
    currency TEXT NOT NULL,
    total_cash_micros INTEGER,
    net_liquidation_micros INTEGER,
    external_net_contributions_micros INTEGER,
    open_put_assignment_obligation_micros INTEGER NOT NULL CHECK (open_put_assignment_obligation_micros >= 0),
    cash_after_all_assignments_micros INTEGER,
    realized_trading_pnl_micros INTEGER,
    unrealized_trading_pnl_micros INTEGER
);

CREATE TABLE IF NOT EXISTS market_bar (
    underlying_id INTEGER NOT NULL REFERENCES underlying(underlying_id),
    session_date TEXT NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d' CHECK (interval IN ('1d')),
    open_micros INTEGER NOT NULL,
    high_micros INTEGER NOT NULL,
    low_micros INTEGER NOT NULL,
    close_micros INTEGER NOT NULL,
    volume INTEGER,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
    PRIMARY KEY (underlying_id, session_date, interval),
    CHECK (high_micros >= open_micros AND high_micros >= close_micros AND high_micros >= low_micros),
    CHECK (low_micros <= open_micros AND low_micros <= close_micros)
);

CREATE TABLE IF NOT EXISTS technical_snapshot (
    underlying_id INTEGER NOT NULL REFERENCES underlying(underlying_id),
    snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
    as_of_session TEXT NOT NULL,
    close_micros INTEGER NOT NULL,
    sma20_micros INTEGER,
    sma50_micros INTEGER,
    sma200_micros INTEGER,
    bollinger_period INTEGER NOT NULL DEFAULT 20,
    bollinger_stddev_micros INTEGER NOT NULL DEFAULT 2000000,
    bollinger_lower_micros INTEGER,
    bollinger_middle_micros INTEGER,
    bollinger_upper_micros INTEGER,
    bollinger_position_millionths INTEGER,
    rsi_period INTEGER NOT NULL DEFAULT 14,
    rsi_millionths INTEGER,
    return_5d_millionths INTEGER,
    return_20d_millionths INTEGER,
    return_60d_millionths INTEGER,
    return_1y_millionths INTEGER,
    stabilization INTEGER NOT NULL CHECK (stabilization IN (0,1)),
    rating TEXT NOT NULL CHECK (rating IN ('STRONG','ACCEPTABLE','CAUTION','REJECT')),
    PRIMARY KEY (underlying_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS reconciliation_issue (
    issue_id TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','HARD_STOP')),
    issue_kind TEXT NOT NULL,
    cycle_id TEXT REFERENCES wheel_cycle(cycle_id),
    snapshot_id TEXT REFERENCES source_snapshot(snapshot_id),
    message TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS no_account_identifier_in_snapshot_reference
BEFORE INSERT ON source_snapshot
WHEN lower(NEW.source_reference) LIKE '%accountid%'
BEGIN
    SELECT RAISE(ABORT, 'source_reference must not contain account identifiers');
END;
