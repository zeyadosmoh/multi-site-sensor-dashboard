# Metric definitions

All metrics are SQL views in [`src/p7/db/sql/views.sql`](../src/p7/db/sql/views.sql). The dashboard, the PDF reports and the tests read them through [`src/p7/metrics.py`](../src/p7/metrics.py), so the screen and the PDF always show the same numbers. Reading times are local Asia/Riyadh wall-clock time (UTC+3, no daylight saving).

| Metric | Definition |
|---|---|
| Expected readings | Minutes in the period ÷ reading interval. With the 60-minute interval that's 24 a day and 744 in a 31-day month. The interval is `app_config.reading_interval_minutes`. |
| Completeness % | Readings received ÷ expected readings × 100. A site with no file for a reported month shows 0 %. Unreliable readings count as received (they arrived), so completeness is always shown next to the number of unreliable readings, e.g. "completeness 99.6 %, 30 unreliable". |
| Reporting months | Months with at least one successfully loaded file, from any site. |
| Valid readings | Readings received minus unreliable readings. |
| Average / min / max | Over **valid** readings only. Averages for longer periods are weighted by valid readings, never an average of daily averages. |
| Out of range | A **valid** reading with temperature outside [min, max] **or** humidity outside [min, max], using the site's current thresholds. A value equal to a limit counts as in range. |
| Minutes out of range | Out-of-range readings × reading interval. |
| Out-of-range event | A run of consecutive out-of-range readings. A missing reading or an unreliable reading ends the event. An event counts on the day and month it starts. |
| Stuck sensor / unreliable reading | A stuck run is consecutive readings, with no gap, where temperature **and** humidity are both identical for at least `stuck_min_hours` (default 6, set per site). Every reading in a stuck run is an **unreliable reading**. |

## Why unreliable readings are excluded

A sensor that repeats the same value for hours is not measuring, so the true temperature during that time is unknown. It could be the middle of a cooling failure. A frozen "23.4 °C" must not make a site look safe.

Unreliable readings are therefore:
- **excluded** from averages, min/max, the hour-of-day profile, out-of-range counts and events;
- **kept** in the database, marked on the site page charts, and listed with their time range;
- **counted** next to completeness on the dashboard and in every PDF;
- **red status:** any stuck run makes the site red for that period.

## Site status

The rules live in `STATUS_RULES` in `metrics.py` and apply to the selected period:

- 🔴 **red** if any of these hold:
  - completeness < 80 %
  - out-of-range readings > 1 % of expected readings
  - at least one stuck run
- 🟡 **amber**, otherwise, if either holds:
  - completeness < 95 %
  - any out-of-range reading
- 🟢 **green** otherwise.

## Threshold changes

Admins edit thresholds on the Thresholds page, and every change is written to `threshold_audit`. Metrics use the new values immediately. Existing PDF reports for that site, and all monthly summaries, are marked **stale** until they are rebuilt. Each PDF prints the thresholds it used.
