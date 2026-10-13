-- One row per shared 1-hour run. Nothing that identifies a player: "install" is a random
-- id made by the tracker, and ip_hash is a hash of the IP address that changes every day
-- (only used to limit how much one connection can send).
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  received      TEXT    NOT NULL,          -- when the server got it (UTC)
  received_day  TEXT    NOT NULL,
  install       TEXT    NOT NULL,
  ip_hash       TEXT    NOT NULL,
  started       TEXT    NOT NULL,          -- when the run started, player's local time
  map           TEXT    NOT NULL,          -- the map name as the tracker read it
  job           TEXT    NOT NULL,
  level_start   INTEGER NOT NULL,
  level_end     INTEGER NOT NULL,
  party         INTEGER NOT NULL,
  exp           REAL    NOT NULL,
  seconds       REAL    NOT NULL,
  exp_hr        REAL    NOT NULL,
  mesos         REAL,
  mesos_seconds REAL,
  mesos_hr      REAL,                       -- NULL when the mesos weren't seen long enough
  version       TEXT,
  UNIQUE (install, started)
);
CREATE INDEX IF NOT EXISTS runs_by_group   ON runs (job, party, level_start);
CREATE INDEX IF NOT EXISTS runs_by_install ON runs (install, started);
CREATE INDEX IF NOT EXISTS runs_by_ip      ON runs (ip_hash, received_day);
