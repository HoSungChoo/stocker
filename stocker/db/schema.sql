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

-- 토스 오픈 API 토큰 공유 캐시. 토큰은 클라이언트당 1개만 유효(재발급하면 이전 토큰 무효)하므로
-- scheduler 컨테이너와 대시보드가 같은 행을 읽고 쓴다.
CREATE TABLE IF NOT EXISTS broker_tokens (
    client_id    text        PRIMARY KEY,
    access_token text        NOT NULL,
    expires_at   timestamptz NOT NULL,
    issued_at    timestamptz NOT NULL DEFAULT now()
);

-- 일별 보유 종목 스냅샷(복기용). 같은 날 다시 받으면 덮어쓴다.
CREATE TABLE IF NOT EXISTS positions_snapshot (
    snapshot_date  date             NOT NULL,
    symbol         text             NOT NULL,
    name           text,
    market_country text,
    currency       text,
    quantity       double precision,
    avg_price      double precision,
    last_price     double precision,
    market_value   double precision,
    pnl            double precision,
    pnl_rate       double precision,
    source         text             NOT NULL DEFAULT 'toss',
    fetched_at     timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, symbol)
);

-- 이벤트 전후 ^SOX 수익률(사실 나열용, 인과 주장 아님). 이벤트가 지워지면 같이 지운다.
CREATE TABLE IF NOT EXISTS event_impact (
    event_id    bigint           PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    ret5        double precision,
    ret_1m      double precision,
    ret_3m      double precision,
    computed_at timestamptz      NOT NULL DEFAULT now()
);

-- 백테스트 결과 (PHASE6_CONTRACT 25절). 모드(baseline/shape/regime)×평가 시점×horizon마다 한 행.
-- median/p10/p90은 그 시점에 낸 이후 수익 분포, realized는 실제 수익률, applicable은 적합 판정.
CREATE TABLE IF NOT EXISTS backtest_runs (
    mode        text             NOT NULL,
    as_of       date             NOT NULL,
    horizon     int              NOT NULL,
    median      double precision,
    p10         double precision,
    p90         double precision,
    n           int,
    applicable  boolean,
    realized    double precision,
    created_at  timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (mode, as_of, horizon)
);
