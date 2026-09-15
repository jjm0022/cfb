CREATE TABLE IF NOT EXISTS games (
    game_id       VARCHAR PRIMARY KEY,
    sport         VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    kickoff_utc   TIMESTAMPTZ NOT NULL,
    home_team_id  VARCHAR NOT NULL,
    away_team_id  VARCHAR NOT NULL,
    home_score    INTEGER,
    away_score    INTEGER
);

CREATE TABLE IF NOT EXISTS league_lines (
    game_id      VARCHAR PRIMARY KEY,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    spread_home  DOUBLE NOT NULL,
    posted_at    TIMESTAMPTZ NOT NULL
);

-- APPEND-ONLY. Never UPDATE or DELETE. Line movement is the signal.
-- The primary key deduplicates identical re-polls without destroying history.
CREATE TABLE IF NOT EXISTS lines (
    game_id      VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    book         VARCHAR NOT NULL,
    spread_home  DOUBLE NOT NULL,
    total        DOUBLE,
    captured_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (game_id, source, book, captured_at)
);

CREATE TABLE IF NOT EXISTS picks (
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    game_id       VARCHAR NOT NULL,
    side          VARCHAR NOT NULL,
    edge_points   DOUBLE NOT NULL,
    tier          VARCHAR NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, week, game_id, generated_at)
);

CREATE TABLE IF NOT EXISTS archive_requests (
    request_id       VARCHAR PRIMARY KEY,
    sport            VARCHAR NOT NULL,
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    kind             VARCHAR NOT NULL,
    requested_at     TIMESTAMPTZ NOT NULL,
    returned_at      TIMESTAMPTZ NOT NULL,
    line_count       INTEGER NOT NULL,
    completed_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_state (
    sport                    VARCHAR NOT NULL,
    season                   INTEGER NOT NULL,
    week                     INTEGER NOT NULL,
    recommendation_signature VARCHAR,
    checked_at               TIMESTAMPTZ,
    error_fingerprint        VARCHAR,
    PRIMARY KEY (sport, season, week)
);

-- One row per entrant per pool week, from a saved CBS Weekly Standings page.
-- A re-import replaces the whole pool week.
CREATE TABLE IF NOT EXISTS pool_results (
    season      INTEGER NOT NULL,
    pool_week   INTEGER NOT NULL,
    entry_id    VARCHAR NOT NULL,
    name        VARCHAR NOT NULL,
    rank        INTEGER NOT NULL,
    points      INTEGER NOT NULL,
    ytd         INTEGER NOT NULL,
    tiebreak    INTEGER,
    imported_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, pool_week, entry_id)
);

CREATE TABLE IF NOT EXISTS pool_picks (
    season       INTEGER NOT NULL,
    pool_week    INTEGER NOT NULL,
    entry_id     VARCHAR NOT NULL,
    game_id      VARCHAR NOT NULL,
    cbs_event_id BIGINT  NOT NULL,
    side         VARCHAR,
    cbs_correct  BOOLEAN,
    PRIMARY KEY (season, pool_week, entry_id, game_id)
);

-- APPEND-ONLY. What the model recommended, and when. Distinct from `picks`,
-- which only `report` writes; see operations/recommendation_history.py.
CREATE TABLE IF NOT EXISTS recommendation_history (
    game_id      VARCHAR NOT NULL,
    sport        VARCHAR NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    side         VARCHAR NOT NULL,
    tier         VARCHAR NOT NULL,
    edge_points  DOUBLE  NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    source       VARCHAR NOT NULL,
    PRIMARY KEY (game_id, generated_at, source)
);
