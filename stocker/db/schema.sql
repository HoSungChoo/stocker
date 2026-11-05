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

SELECT create_hypertable('prices_daily', 'date', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS macro_series (
    series_id   text             NOT NULL,
    date        date             NOT NULL,
    value       double precision,
    source      text,
    ingested_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, date)
);

SELECT create_hypertable('macro_series', 'date', if_not_exists => TRUE);

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
