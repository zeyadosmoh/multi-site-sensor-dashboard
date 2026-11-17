-- Metric views. Re-created on every `p7 migrate` (they hold no data).
-- Definitions (also in docs/metrics.md):
--   expected readings   = minutes in the period / reading interval (60 -> 24 a day, 744 in a 31-day month)
--   out of range        = temperature outside [temp_min, temp_max] OR humidity outside [hum_min, hum_max]
--                         (bounds are inclusive: a reading equal to the limit is in range); thresholds are the
--                         site's CURRENT ones
--   minutes out of range = out-of-range readings x reading interval
--   out-of-range event  = a run of out-of-range readings with no gap (consecutive intervals); a missing reading
--                         ends the event. An event belongs to the month (and day) it starts in.
--   stuck run           = consecutive readings (no gap) with identical temperature AND humidity lasting
--                         >= stuck_min_hours. Every reading in it is an UNRELIABLE reading: stored and flagged,
--                         counted next to completeness, but excluded from averages, min/max and out-of-range
--                         counts (the true value is unknown). An unreliable reading also ends an out-of-range event.
--   completeness %      = readings received / expected (unreliable ones included: they arrived, but are shown
--                         separately as "unreliable")
--   reporting months    = months that have at least one successfully loaded upload (any site)

DROP VIEW IF EXISTS v_site_month, v_site_day, v_oor_events, v_stuck_runs, v_readings_flagged, v_calendar_day,
    v_reporting_months CASCADE;

CREATE VIEW v_reporting_months AS
SELECT DISTINCT month FROM uploads WHERE status = 'loaded' AND month IS NOT NULL;

CREATE VIEW v_calendar_day AS
SELECT s.site_id, d::date AS day, date_trunc('month', d)::date AS month
FROM sites s
CROSS JOIN v_reporting_months m
CROSS JOIN LATERAL generate_series(m.month, (m.month + interval '1 month' - interval '1 day'), interval '1 day') d;

CREATE VIEW v_readings_flagged AS
WITH cfg AS (
    SELECT make_interval(mins => reading_interval_minutes) AS step, reading_interval_minutes AS step_min
    FROM app_config
), base AS (
    SELECT r.site_id, r.reading_ts, r.temperature, r.humidity, s.stuck_min_hours, cfg.step_min,
           (r.temperature < s.temp_min OR r.temperature > s.temp_max) AS raw_temp_out,
           (r.humidity < s.hum_min OR r.humidity > s.hum_max) AS raw_hum_out,
           coalesce(lag(r.reading_ts) OVER w = r.reading_ts - cfg.step, false) AS follows_prev,
           lag(r.temperature) OVER w AS prev_t,
           lag(r.humidity) OVER w AS prev_h
    FROM readings r JOIN sites s USING (site_id) CROSS JOIN cfg
    WINDOW w AS (PARTITION BY r.site_id ORDER BY r.reading_ts)
), same_runs AS (
    SELECT b.*,
           sum(CASE WHEN b.follows_prev AND b.temperature = b.prev_t AND b.humidity = b.prev_h THEN 0 ELSE 1 END)
               OVER (PARTITION BY b.site_id ORDER BY b.reading_ts) AS same_grp
    FROM base b
), stuck AS (
    -- a stuck (unreliable) reading is not a measurement: it is never in range or out of range
    SELECT sr.*,
           count(*) OVER (PARTITION BY sr.site_id, sr.same_grp) * sr.step_min >= sr.stuck_min_hours * 60 AS stuck
    FROM same_runs sr
), flagged AS (
    SELECT st.*,
           st.raw_temp_out AND NOT st.stuck AS temp_out,
           st.raw_hum_out AND NOT st.stuck AS hum_out,
           (st.raw_temp_out OR st.raw_hum_out) AND NOT st.stuck AS out_of_range
    FROM stuck st
), prev_oor AS (
    SELECT f.*, coalesce(lag(f.out_of_range) OVER (PARTITION BY f.site_id ORDER BY f.reading_ts), false) AS prev_out
    FROM flagged f
), oor AS (
    SELECT po.*,
           sum(CASE WHEN po.out_of_range AND NOT (po.follows_prev AND po.prev_out) THEN 1 ELSE 0 END)
               OVER (PARTITION BY po.site_id ORDER BY po.reading_ts) AS oor_grp_all
    FROM prev_oor po
)
SELECT o.site_id, o.reading_ts, o.reading_ts::date AS day, date_trunc('month', o.reading_ts)::date AS month,
       extract(hour FROM o.reading_ts)::int AS hour,
       o.temperature, o.humidity, o.temp_out, o.hum_out, o.out_of_range, o.step_min,
       CASE WHEN o.out_of_range THEN o.oor_grp_all END AS oor_grp,
       o.same_grp, o.stuck
FROM oor o;

CREATE VIEW v_oor_events AS
SELECT site_id, oor_grp AS event_no,
       min(reading_ts) AS start_ts, max(reading_ts) AS end_ts,
       min(reading_ts)::date AS day, date_trunc('month', min(reading_ts))::date AS month,
       count(*) AS readings, count(*) * min(step_min) AS minutes,
       count(*) FILTER (WHERE temp_out) AS temp_out_readings,
       count(*) FILTER (WHERE hum_out) AS hum_out_readings,
       max(temperature) AS max_temperature, min(temperature) AS min_temperature,
       max(humidity) AS max_humidity, min(humidity) AS min_humidity
FROM v_readings_flagged
WHERE out_of_range
GROUP BY site_id, oor_grp;

CREATE VIEW v_stuck_runs AS
SELECT site_id, same_grp AS run_no, min(reading_ts) AS start_ts, max(reading_ts) AS end_ts,
       date_trunc('month', min(reading_ts))::date AS month,
       count(*) AS readings, min(temperature) AS temperature, min(humidity) AS humidity
FROM v_readings_flagged
WHERE stuck
GROUP BY site_id, same_grp;

CREATE VIEW v_site_day AS
WITH cfg AS (SELECT reading_interval_minutes AS step_min FROM app_config),
agg AS (
    SELECT site_id, day, count(*) AS readings,
           count(*) FILTER (WHERE NOT stuck) AS valid_readings,
           avg(temperature) FILTER (WHERE NOT stuck) AS temp_avg,
           min(temperature) FILTER (WHERE NOT stuck) AS temp_min,
           max(temperature) FILTER (WHERE NOT stuck) AS temp_max,
           avg(humidity) FILTER (WHERE NOT stuck) AS hum_avg,
           min(humidity) FILTER (WHERE NOT stuck) AS hum_min,
           max(humidity) FILTER (WHERE NOT stuck) AS hum_max,
           count(*) FILTER (WHERE out_of_range) AS oor_readings,
           count(*) FILTER (WHERE temp_out) AS temp_out_readings,
           count(*) FILTER (WHERE hum_out) AS hum_out_readings,
           count(*) FILTER (WHERE stuck) AS unreliable_readings
    FROM v_readings_flagged GROUP BY site_id, day
), ev AS (
    SELECT site_id, day, count(*) AS oor_events FROM v_oor_events GROUP BY site_id, day
)
SELECT c.site_id, c.day, c.month,
       coalesce(a.readings, 0) AS readings,
       coalesce(a.valid_readings, 0) AS valid_readings,
       1440 / cfg.step_min AS expected_readings,
       round(100.0 * coalesce(a.readings, 0) / (1440 / cfg.step_min), 2) AS completeness_pct,
       a.temp_avg, a.temp_min, a.temp_max, a.hum_avg, a.hum_min, a.hum_max,
       coalesce(a.oor_readings, 0) AS oor_readings,
       coalesce(a.oor_readings, 0) * cfg.step_min AS oor_minutes,
       coalesce(a.temp_out_readings, 0) AS temp_out_readings,
       coalesce(a.hum_out_readings, 0) AS hum_out_readings,
       coalesce(ev.oor_events, 0) AS oor_events,
       coalesce(a.unreliable_readings, 0) AS unreliable_readings
FROM v_calendar_day c
CROSS JOIN cfg
LEFT JOIN agg a ON a.site_id = c.site_id AND a.day = c.day
LEFT JOIN ev ON ev.site_id = c.site_id AND ev.day = c.day;

CREATE VIEW v_site_month AS
WITH cfg AS (SELECT reading_interval_minutes AS step_min FROM app_config),
agg AS (
    SELECT site_id, month, count(*) AS readings,
           count(*) FILTER (WHERE NOT stuck) AS valid_readings,
           avg(temperature) FILTER (WHERE NOT stuck) AS temp_avg,
           min(temperature) FILTER (WHERE NOT stuck) AS temp_min,
           max(temperature) FILTER (WHERE NOT stuck) AS temp_max,
           avg(humidity) FILTER (WHERE NOT stuck) AS hum_avg,
           min(humidity) FILTER (WHERE NOT stuck) AS hum_min,
           max(humidity) FILTER (WHERE NOT stuck) AS hum_max,
           count(*) FILTER (WHERE out_of_range) AS oor_readings,
           count(*) FILTER (WHERE temp_out) AS temp_out_readings,
           count(*) FILTER (WHERE hum_out) AS hum_out_readings,
           count(*) FILTER (WHERE stuck) AS unreliable_readings
    FROM v_readings_flagged GROUP BY site_id, month
), ev AS (
    SELECT site_id, month, count(*) AS oor_events, max(minutes) AS longest_event_minutes
    FROM v_oor_events GROUP BY site_id, month
), st AS (
    SELECT site_id, month, count(*) AS stuck_runs FROM v_stuck_runs GROUP BY site_id, month
), m AS (
    SELECT s.site_id, rm.month,
           (extract(epoch FROM (rm.month + interval '1 month') - rm.month) / 60 / cfg.step_min)::int AS expected
    FROM sites s CROSS JOIN v_reporting_months rm CROSS JOIN cfg
)
SELECT m.site_id, m.month,
       coalesce(a.readings, 0) AS readings,
       coalesce(a.valid_readings, 0) AS valid_readings,
       m.expected AS expected_readings,
       round(100.0 * coalesce(a.readings, 0) / m.expected, 2) AS completeness_pct,
       a.temp_avg, a.temp_min, a.temp_max, a.hum_avg, a.hum_min, a.hum_max,
       coalesce(a.oor_readings, 0) AS oor_readings,
       coalesce(a.oor_readings, 0) * cfg.step_min AS oor_minutes,
       coalesce(a.temp_out_readings, 0) AS temp_out_readings,
       coalesce(a.hum_out_readings, 0) AS hum_out_readings,
       coalesce(ev.oor_events, 0) AS oor_events,
       coalesce(ev.longest_event_minutes, 0) AS longest_event_minutes,
       coalesce(a.unreliable_readings, 0) AS unreliable_readings,
       coalesce(st.stuck_runs, 0) AS stuck_runs
FROM m
CROSS JOIN cfg
LEFT JOIN agg a ON a.site_id = m.site_id AND a.month = m.month
LEFT JOIN ev ON ev.site_id = m.site_id AND ev.month = m.month
LEFT JOIN st ON st.site_id = m.site_id AND st.month = m.month;
