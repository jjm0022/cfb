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
