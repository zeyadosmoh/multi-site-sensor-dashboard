-- P7 core schema. All timestamps of readings are local wall-clock time in Asia/Riyadh (UTC+3, no DST),
-- stored as `timestamp` so day/hour grouping never depends on the session time zone.

CREATE TABLE app_config (
    id                        smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    reading_interval_minutes  int NOT NULL DEFAULT 60 CHECK (reading_interval_minutes BETWEEN 1 AND 1440)
);
INSERT INTO app_config DEFAULT VALUES;

CREATE TABLE sites (
    site_id          serial PRIMARY KEY,
    code             text NOT NULL UNIQUE CHECK (code ~ '^[A-Z]{3}-[0-9]{2}$'),
    name             text NOT NULL,
    city             text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('warehouse', 'branch')),
    lat              numeric(8, 5) NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon              numeric(8, 5) NOT NULL CHECK (lon BETWEEN -180 AND 180),
    temp_min         numeric(5, 1) NOT NULL,
    temp_max         numeric(5, 1) NOT NULL,
    hum_min          numeric(5, 1) NOT NULL,
    hum_max          numeric(5, 1) NOT NULL,
    stuck_min_hours  int NOT NULL DEFAULT 6 CHECK (stuck_min_hours BETWEEN 2 AND 168),
    CHECK (temp_min < temp_max),
    CHECK (hum_min < hum_max AND hum_min >= 0 AND hum_max <= 100)
);

CREATE TABLE users (
    user_id        serial PRIMARY KEY,
    username       text NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_.-]{3,40}$'),
    password_hash  text NOT NULL,
    role           text NOT NULL CHECK (role IN ('admin', 'management')),
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);

-- One row per uploaded file (also failed / duplicate ones). Written in its own transaction.
CREATE TABLE uploads (
    upload_id      bigserial PRIMARY KEY,
    batch_id       uuid NOT NULL,
    user_id        int REFERENCES users (user_id),
    source         text NOT NULL CHECK (source IN ('web', 'cli')),
    filename       text NOT NULL,
    archive_name   text,                       -- the .zip it came from, if any
    sha256         char(64) NOT NULL,
    size_bytes     bigint NOT NULL,
    site_id        int REFERENCES sites (site_id),
    month          date CHECK (month = date_trunc('month', month)),
    detected_by    text CHECK (detected_by IN ('filename', 'cover_sheet', 'filename+cover_sheet')),
    status         text NOT NULL CHECK (status IN ('processing', 'loaded', 'duplicate_file', 'rejected_file', 'failed')),
    rows_total     int NOT NULL DEFAULT 0,
    inserted       int NOT NULL DEFAULT 0,
    updated        int NOT NULL DEFAULT 0,
    unchanged      int NOT NULL DEFAULT 0,
    rejected       int NOT NULL DEFAULT 0,
    warnings       jsonb NOT NULL DEFAULT '[]',
    error          text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
-- A given file content is loaded at most once.
CREATE UNIQUE INDEX uploads_loaded_sha256 ON uploads (sha256) WHERE status = 'loaded';
CREATE INDEX uploads_site_month ON uploads (site_id, month);

CREATE TABLE readings (
    site_id      int NOT NULL REFERENCES sites (site_id),
    reading_ts   timestamp NOT NULL CHECK (reading_ts = date_trunc('minute', reading_ts)),
    temperature  numeric(5, 1) NOT NULL CHECK (temperature BETWEEN -40 AND 70),
    humidity     numeric(5, 1) NOT NULL CHECK (humidity BETWEEN 0 AND 100),
    upload_id    bigint NOT NULL REFERENCES uploads (upload_id),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, reading_ts)
);

-- Every rejected row, with the sheet row number and raw cell text, and a reason code.
CREATE TABLE row_rejects (
    reject_id        bigserial PRIMARY KEY,
    upload_id        bigint NOT NULL REFERENCES uploads (upload_id) ON DELETE CASCADE,
    sheet            text NOT NULL,
    row_number       int NOT NULL,
    raw_date         text,
    raw_time         text,
    raw_temperature  text,
    raw_humidity     text,
    reason           text NOT NULL,
    message          text NOT NULL
);
CREATE INDEX row_rejects_upload ON row_rejects (upload_id);

CREATE TABLE threshold_audit (
    audit_id    bigserial PRIMARY KEY,
    site_id     int NOT NULL REFERENCES sites (site_id),
    user_id     int REFERENCES users (user_id),
    changed_at  timestamptz NOT NULL DEFAULT now(),
    old_values  jsonb NOT NULL,
    new_values  jsonb NOT NULL
);

CREATE TABLE reports (
    report_id     bigserial PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('site_month', 'summary_month')),
    site_id       int REFERENCES sites (site_id),
    month         date NOT NULL CHECK (month = date_trunc('month', month)),
    lang          text NOT NULL CHECK (lang IN ('en', 'ar')),
    pdf_path      text NOT NULL,
    facts         jsonb NOT NULL,
    generated_at  timestamptz NOT NULL DEFAULT now(),
    stale         boolean NOT NULL DEFAULT false,
    CHECK ((kind = 'site_month') = (site_id IS NOT NULL))
);
CREATE UNIQUE INDEX reports_one_per_key ON reports (kind, coalesce(site_id, 0), month, lang);
