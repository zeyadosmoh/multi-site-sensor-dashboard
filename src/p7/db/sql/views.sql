-- Metric views. Re-created on every `p7 migrate` (they hold no data).
-- Definitions (also in docs/metrics.md):
--   expected readings   = minutes in the period / reading interval (60 -> 24 a day, 744 in a 31-day month)
--   completeness %      = readings stored / expected readings x 100
--   out of range        = temperature outside [temp_min, temp_max] OR humidity outside [hum_min, hum_max]
--                         (bounds are inclusive: a reading equal to the limit is in range); thresholds are the
--                         site's CURRENT ones
--   minutes out of range = out-of-range readings x reading interval
--   out-of-range event  = a run of out-of-range readings with no gap (consecutive intervals); a missing reading
--                         ends the event. An event belongs to the month (and day) it starts in.
--   stuck run           = consecutive readings (no gap) with identical temperature AND humidity lasting
--                         >= stuck_min_hours; every reading in it is flagged stuck. Stuck readings stay in the
--                         averages (they are stored data) but are flagged on the dashboard and in reports.
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
    SELECT r.site_id, r.reading_ts, r.temperature, r.humidity, s.stuck_min_hours, cfg.step, cfg.step_min,
           (r.temperature < s.temp_min OR r.temperature > s.temp_max) AS temp_out,
           (r.humidity < s.hum_min OR r.humidity > s.hum_max) AS hum_out
    FROM readings r JOIN sites s USING (site_id) CROSS JOIN cfg
), lagged AS (
    SELECT b.*,
           (b.temp_out OR b.hum_out) AS out_of_range,
           lag(b.reading_ts)  OVER w = b.reading_ts - b.step AS follows_prev,
           lag(b.temperature) OVER w AS prev_t,
           lag(b.humidity)    OVER w AS prev_h,
           lag(b.temp_out OR b.hum_out) OVER w AS prev_oor
    FROM base b
    WINDOW w AS (PARTITION BY b.site_id ORDER BY b.reading_ts)
), islands AS (
    SELECT l.*,
           sum(CASE WHEN coalesce(l.follows_prev, false) AND l.temperature = l.prev_t AND l.humidity = l.prev_h
                    THEN 0 ELSE 1 END) OVER w AS same_grp,
           sum(CASE WHEN l.out_of_range AND NOT (coalesce(l.follows_prev, false) AND coalesce(l.prev_oor, false))
                    THEN 1 ELSE 0 END) OVER w AS oor_grp
    FROM lagged l
    WINDOW w AS (PARTITION BY l.site_id ORDER BY l.reading_ts)
)
SELECT i.site_id, i.reading_ts, i.reading_ts::date AS day, date_trunc('month', i.reading_ts)::date AS month,
       extract(hour FROM i.reading_ts)::int AS hour,
       i.temperature, i.humidity, i.temp_out, i.hum_out, i.out_of_range, i.step_min,
       CASE WHEN i.out_of_range THEN i.oor_grp END AS oor_grp,
       i.same_grp,
       count(*) OVER (PARTITION BY i.site_id, i.same_grp) * i.step_min >= i.stuck_min_hours * 60 AS stuck
FROM islands i;

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
           avg(temperature) AS temp_avg, min(temperature) AS temp_min, max(temperature) AS temp_max,
           avg(humidity) AS hum_avg, min(humidity) AS hum_min, max(humidity) AS hum_max,
           count(*) FILTER (WHERE out_of_range) AS oor_readings,
           count(*) FILTER (WHERE temp_out) AS temp_out_readings,
           count(*) FILTER (WHERE hum_out) AS hum_out_readings,
           count(*) FILTER (WHERE stuck) AS stuck_readings
    FROM v_readings_flagged GROUP BY site_id, day
), ev AS (
    SELECT site_id, day, count(*) AS oor_events FROM v_oor_events GROUP BY site_id, day
)
SELECT c.site_id, c.day, c.month,
       coalesce(a.readings, 0) AS readings,
       1440 / cfg.step_min AS expected_readings,
       round(100.0 * coalesce(a.readings, 0) / (1440 / cfg.step_min), 2) AS completeness_pct,
       a.temp_avg, a.temp_min, a.temp_max, a.hum_avg, a.hum_min, a.hum_max,
       coalesce(a.oor_readings, 0) AS oor_readings,
       coalesce(a.oor_readings, 0) * cfg.step_min AS oor_minutes,
       coalesce(a.temp_out_readings, 0) AS temp_out_readings,
       coalesce(a.hum_out_readings, 0) AS hum_out_readings,
       coalesce(ev.oor_events, 0) AS oor_events,
       coalesce(a.stuck_readings, 0) AS stuck_readings
FROM v_calendar_day c
CROSS JOIN cfg
LEFT JOIN agg a ON a.site_id = c.site_id AND a.day = c.day
LEFT JOIN ev ON ev.site_id = c.site_id AND ev.day = c.day;

CREATE VIEW v_site_month AS
WITH cfg AS (SELECT reading_interval_minutes AS step_min FROM app_config),
agg AS (
    SELECT site_id, month, count(*) AS readings,
           avg(temperature) AS temp_avg, min(temperature) AS temp_min, max(temperature) AS temp_max,
           avg(humidity) AS hum_avg, min(humidity) AS hum_min, max(humidity) AS hum_max,
           count(*) FILTER (WHERE out_of_range) AS oor_readings,
           count(*) FILTER (WHERE temp_out) AS temp_out_readings,
           count(*) FILTER (WHERE hum_out) AS hum_out_readings,
           count(*) FILTER (WHERE stuck) AS stuck_readings
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
       m.expected AS expected_readings,
       round(100.0 * coalesce(a.readings, 0) / m.expected, 2) AS completeness_pct,
       a.temp_avg, a.temp_min, a.temp_max, a.hum_avg, a.hum_min, a.hum_max,
       coalesce(a.oor_readings, 0) AS oor_readings,
       coalesce(a.oor_readings, 0) * cfg.step_min AS oor_minutes,
       coalesce(a.temp_out_readings, 0) AS temp_out_readings,
       coalesce(a.hum_out_readings, 0) AS hum_out_readings,
       coalesce(ev.oor_events, 0) AS oor_events,
       coalesce(ev.longest_event_minutes, 0) AS longest_event_minutes,
       coalesce(a.stuck_readings, 0) AS stuck_readings,
       coalesce(st.stuck_runs, 0) AS stuck_runs
FROM m
CROSS JOIN cfg
LEFT JOIN agg a ON a.site_id = m.site_id AND a.month = m.month
LEFT JOIN ev ON ev.site_id = m.site_id AND ev.month = m.month
LEFT JOIN st ON st.site_id = m.site_id AND st.month = m.month;
