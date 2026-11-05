-- 멱등 스키마. 여러 번 실행해도 안전하다.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS prices_daily (
    symbol      text             NOT NULL,
    date        date             NOT NULL,
    open        double precision,
    high        double precision,
    low         double precision,
    close       double precision,
    adj_close   double precision,
    volume      bigint,
    source      text,
    ingested_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('prices_daily', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '5 years');

CREATE TABLE IF NOT EXISTS macro_series (
    series_id   text             NOT NULL,
    date        date             NOT NULL,
    value       double precision,
    source      text,
    ingested_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, date)
);

SELECT create_hypertable('macro_series', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '5 years');

CREATE TABLE IF NOT EXISTS events (
    id          bigserial PRIMARY KEY,
    date        date      NOT NULL,
    kind        text      NOT NULL,
    title       text,
    symbol      text,
    scheduled   boolean,
    source      text,
    meta        jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS events_uniq
    ON events (date, kind, (coalesce(symbol, '')), title);

CREATE TABLE IF NOT EXISTS semi_cycle (
    metric      text             NOT NULL,
    date        date             NOT NULL,
    value       double precision,
    source      text,
    ingested_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (metric, date)
);

SELECT create_hypertable('semi_cycle', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '5 years');

-- 아침 보고서 실행 기록. window=0 행에 전체 Markdown, window>0 행에 창별 결과(JSON).
CREATE TABLE IF NOT EXISTS report_runs (
    run_date    date        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    "window"    int         NOT NULL,
    result      jsonb,
    report_md   text,
    PRIMARY KEY (run_date, "window")
);
