# Deployment guide

The app is two containers: PostgreSQL 16 and the web app. The web app image already contains Chromium, which it uses to make the PDFs. Both containers publish their ports on `127.0.0.1` only. To reach the app from other machines, put an HTTPS reverse proxy in front of it (see [below](#4-https-and-access-from-other-machines)).

| Service | Container | Host port | Notes |
|---|---|---|---|
| PostgreSQL 16 | `p7-sensors-pg` | `127.0.0.1:5437` | data in the `p7_sensors_pgdata` volume |
| Web app | `p7-sensors-app` | `127.0.0.1:8790` | PDFs in the `p7_sensors_reports` volume |

## 1. Prerequisites

- Docker Engine with the Compose plugin
- About 3 GB of disk for the images. The Playwright base image is the large one.
- Python 3.12+ on the host, only for `setup-env` and for generating the synthetic demo data. You can also write `.env` by hand from `.env.example`.

## 2. Configure

```bash
git clone https://github.com/zeyadosmoh/multi-site-sensor-dashboard.git
cd multi-site-sensor-dashboard
python3 -m venv .venv && .venv/bin/pip install -e .   # project-local; nothing installed system-wide
.venv/bin/python -m p7 setup-env   # or: cp .env.example .env and fill in every "change-me"
```

`setup-env` writes `.env` with mode `600`. It fills in random values for these three settings:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | database password |
| `SESSION_SECRET` | signs the session cookie; at least 32 characters (the app refuses to start without it) |
| `ADMIN_PASSWORD` | password of the first admin account (`ADMIN_USERNAME`, default `admin`) |

Other settings:
- `MAX_UPLOAD_MB` is the per-file upload limit (default 50).
- `COOKIE_SECURE=true` must be set once the app is served over HTTPS.

**Never commit `.env`.** The `.gitignore` already excludes it.

## 3. Start

```bash
docker compose --profile app up -d --build
docker compose ps                         # both services "healthy"
curl -s http://127.0.0.1:8790/healthz     # {"ok":true}
```

On every start the app container:
1. applies database migrations (safe to run again);
2. creates the first admin from `ADMIN_USERNAME` / `ADMIN_PASSWORD` if that user doesn't exist yet;
3. starts the web server.

Open <http://127.0.0.1:8790> and sign in as the admin.

### Load the demo data (optional)

```bash
.venv/bin/python -m p7 generate --sample-month 2026-08    # 78 synthetic Excel files + manifest.json
```

Upload `data/synthetic/xlsx/*.xlsx` (or `data/sample/sensors_2026-08.zip`) on the **Uploads** page. You can also load them from the host with `.venv/bin/python -m p7 ingest data/synthetic/xlsx`. To check the result against the generator's ground truth, run `python -m p7 verify-manifest`.

### Add management users

As admin, go to **Users → Add user** and choose the role *Management*. Management users can view everything and download PDFs and CSVs, but they can't upload files, change thresholds or manage users.

From the command line:

```bash
docker compose exec app python -m p7 create-user manager1 --role management   # prompts for the password
```

## 4. HTTPS and access from other machines

Keep the app on `127.0.0.1:8790` and put a reverse proxy on the same host. Caddy is shown here; nginx works the same way.

```caddyfile
sensors.example.internal {
    reverse_proxy 127.0.0.1:8790
    request_body {
        max_size 60MB
    }
}
```

Then set `COOKIE_SECURE=true` in `.env` and run `docker compose --profile app up -d`.

The app sets a strict Content-Security-Policy and other security headers itself. The proxy only needs to terminate TLS.

### Map tiles

The site map loads tiles from `tile.openstreetmap.org` in the viewer's browser. No API key is needed. That is fine for a small internal dashboard under OpenStreetMap's [tile usage policy](https://operations.osmfoundation.org/policies/tiles/). For heavy use, point the tile URL in `src/p7/web/static/app.js` at your own tile server or a provider, and add that host to `img-src` in the CSP in `src/p7/web/app.py`.

## 5. Backups and restore

```bash
# backup (consistent, while running)
docker compose exec -T postgres pg_dump -U p7 -d p7 -Fc > backup_$(date +%F).dump
# restore into an empty database
docker compose exec -T postgres pg_restore -U p7 -d p7 --clean --if-exists < backup_2026-11-17.dump
docker compose restart app   # re-applies migrations and refreshes the metric views
```

- The original Excel files are not kept, only their SHA-256 hashes and the validated rows. Keep the source files in your own archive.
- PDFs can always be rebuilt from the database (Reports → *Rebuild all reports for this month*), so their volume doesn't need a backup.

## 6. Updates

```bash
git pull
docker compose --profile app up -d --build   # migrations run on start
```

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| App container keeps restarting | `docker compose logs app`. The usual cause is a missing or short `SESSION_SECRET`, or a wrong `POSTGRES_PASSWORD`. |
| Sign-in works but you are signed straight out again | `COOKIE_SECURE=true` while serving over plain HTTP |
| "Too many failed attempts" | Five wrong passwords in 5 minutes for that user and IP. Wait, or restart the app. |
| A PDF is marked *stale* | Thresholds changed, or a newer file was loaded for that month. Downloading it rebuilds it. |
| Upload "rejected" | The reason is shown next to the file, and the full upload log is on the Uploads page. Rejected rows can be downloaded as CSV from the log. |
| Port already in use | Change `POSTGRES_PORT` / `APP_PORT` in `.env`. |

## 8. Running without Docker (development)

```bash
docker compose up -d postgres            # just the database
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium   # user-level browser cache; no system packages
.venv/bin/python -m p7 migrate && .venv/bin/python -m p7 create-admin
.venv/bin/python -m p7 serve             # http://127.0.0.1:8790
.venv/bin/python -m pytest               # tests use a separate database, p7_test
```
