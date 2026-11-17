# Metric definitions

All metrics are SQL views in [`src/p7/db/sql/views.sql`](../src/p7/db/sql/views.sql). The dashboard, the PDF reports and the tests read them through [`src/p7/metrics.py`](../src/p7/metrics.py), so the screen and the PDF always show the same numbers. Reading times are local Asia/Riyadh wall-clock time (UTC+3, no daylight saving).

| Metric | Definition |
|---|---|
| Expected readings | Minutes in the period ÷ reading interval. With the 60-minute interval that's 24 a day and 744 in a 31-day month. The interval is `app_config.reading_interval_minutes`. |
| Completeness % | Readings stored ÷ expected readings × 100. A site with no file for a reported month shows 0 %. |
| Reporting months | Months with at least one successfully loaded file, from any site. |
| Average / min / max | Over stored readings. Averages for longer periods are weighted by readings, never an average of daily averages. |
| Out of range | Temperature outside [min, max] **or** humidity outside [min, max], using the site's current thresholds. A value equal to a limit counts as in range. |
| Minutes out of range | Out-of-range readings × reading interval. |
| Out-of-range event | A run of consecutive out-of-range readings. A missing reading ends the event. An event counts on the day and month it starts. |
| Stuck sensor | Consecutive readings, with no gap, where temperature **and** humidity are both identical for at least `stuck_min_hours` (default 6, set per site). Stuck readings stay in the averages because they are stored data, but they are flagged and counted. |

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
