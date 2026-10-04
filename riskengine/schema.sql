-- One row per pipeline run. Every other table points back here.
CREATE TABLE IF NOT EXISTS run_log (
    run_id        VARCHAR PRIMARY KEY,
    as_of         DATE NOT NULL,
    mode          VARCHAR NOT NULL,      -- live | offline
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        VARCHAR,               -- PASS | WARN | FAIL | ERROR
    code_version  VARCHAR,
    config_sha256 VARCHAR
);

-- Where each batch of raw data came from, and a hash of the exact bytes.
CREATE TABLE IF NOT EXISTS lineage (
    fetch_id   VARCHAR PRIMARY KEY,
    run_id     VARCHAR NOT NULL,
    source     VARCHAR NOT NULL,
    request    VARCHAR NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    raw_path   VARCHAR NOT NULL,
    sha256     VARCHAR NOT NULL,
    n_rows     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_instrument (
    instrument_id VARCHAR PRIMARY KEY,
    name          VARCHAR,
    asset_class   VARCHAR,
    desk          VARCHAR,
    currency      VARCHAR,
    quote         VARCHAR,
    source        VARCHAR,
    ticker        VARCHAR,
    tenor_years   DOUBLE
);

-- Cleaned, normalised market data (GBX already converted to GBP).
CREATE TABLE IF NOT EXISTS market_data (
    instrument_id VARCHAR NOT NULL,
    date          DATE NOT NULL,
    close         DOUBLE NOT NULL,
    adj_close     DOUBLE NOT NULL,
    fetch_id      VARCHAR NOT NULL,
    run_id        VARCHAR NOT NULL,
    PRIMARY KEY (instrument_id, date)
);

-- Observations from other series, tied to an instrument:
--   cross_check  - a second source for the same quantity (checks the primary)
--   return_proxy - a series whose returns stand in for the instrument's in risk scenarios
CREATE TABLE IF NOT EXISTS reference_prices (
    instrument_id VARCHAR NOT NULL,
    purpose       VARCHAR NOT NULL,
    source        VARCHAR NOT NULL,
    ticker        VARCHAR NOT NULL,
    date          DATE NOT NULL,
    value         DOUBLE NOT NULL,
    fetch_id      VARCHAR NOT NULL,
    PRIMARY KEY (instrument_id, purpose, date)
);

-- Cash dividends per share, in the instrument's currency (pence already converted).
CREATE TABLE IF NOT EXISTS dividends (
    instrument_id VARCHAR NOT NULL,
    ex_date       DATE NOT NULL,
    amount        DOUBLE NOT NULL,
    fetch_id      VARCHAR NOT NULL,
    PRIMARY KEY (instrument_id, ex_date)
);

-- End-of-day position snapshot, as the position system reports it.
CREATE TABLE IF NOT EXISTS positions (
    as_of         DATE NOT NULL,
    instrument_id VARCHAR NOT NULL,
    quantity      DOUBLE NOT NULL,
    run_id        VARCHAR NOT NULL,
    PRIMARY KEY (as_of, instrument_id)
);

-- Trades, as the trade capture system reports them. Reconciled against positions.
CREATE TABLE IF NOT EXISTS trades (
    trade_id      VARCHAR PRIMARY KEY,
    trade_date    DATE NOT NULL,
    instrument_id VARCHAR NOT NULL,
    quantity      DOUBLE NOT NULL,
    run_id        VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS control_results (
    run_id     VARCHAR NOT NULL,
    as_of      DATE NOT NULL,
    control    VARCHAR NOT NULL,
    status     VARCHAR NOT NULL,         -- PASS | WARN | FAIL | INFO
    n_affected INTEGER NOT NULL,
    detail     VARCHAR
);

-- Risk measures. One row per (run, method, measure, scope).
CREATE TABLE IF NOT EXISTS risk_results (
    run_id     VARCHAR NOT NULL,
    as_of      DATE NOT NULL,
    method     VARCHAR NOT NULL,        -- hs_equal | hs_weighted | parametric
    measure    VARCHAR NOT NULL,        -- VaR95 | VaR99 | ES975 | ES975_contribution | VaR99_standalone
    scope      VARCHAR NOT NULL,        -- total | desk
    scope_id   VARCHAR NOT NULL,
    value_gbp  DOUBLE NOT NULL
);

-- Daily backtest: yesterday's VaR forecast against today's hypothetical P&L.
CREATE TABLE IF NOT EXISTS backtest (
    run_id     VARCHAR NOT NULL,
    date       DATE NOT NULL,
    method     VARCHAR NOT NULL,
    var95      DOUBLE NOT NULL,
    var99      DOUBLE NOT NULL,
    pnl        DOUBLE NOT NULL
);

-- Stress results: P&L of today's book under each scenario, by desk.
CREATE TABLE IF NOT EXISTS stress_results (
    run_id      VARCHAR NOT NULL,
    as_of       DATE NOT NULL,
    scenario    VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL,       -- historical | hypothetical
    desk        VARCHAR NOT NULL,       -- a desk name, or 'total'
    pnl_gbp     DOUBLE NOT NULL
);

-- Day-over-day VaR explain (Shapley split over positions, market levels and scenario window).
CREATE TABLE IF NOT EXISTS var_explain (
    run_id      VARCHAR NOT NULL,
    as_of       DATE NOT NULL,
    prev_date   DATE NOT NULL,
    measure     VARCHAR NOT NULL,       -- VaR99 | ES975
    component   VARCHAR NOT NULL,       -- previous | positions | levels | window | current
    value_gbp   DOUBLE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_data_date ON market_data (date);
