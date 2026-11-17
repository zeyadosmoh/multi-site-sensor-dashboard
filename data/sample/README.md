# Sample month (SYNTHETIC)

`sensors_2026-08.zip` holds the 13 August 2026 files, exactly as `python -m p7 generate --sample-month 2026-08` (seed 7) writes them. CI regenerates the zip and fails if it differs by a single byte.

All other months are regenerated from the seed rather than committed.

What this month contains:
- KHB-01's stuck sensor (30 hours);
- JED-01's 56-hour cooling failure;
- a file named `sensor_export_final (2).xlsx` that is identified only by its Info cover sheet (ABH-01).

Every reading is generated. None of it is real sensor data.
