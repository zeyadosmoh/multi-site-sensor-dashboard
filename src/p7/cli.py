"""Command line: python -m p7 <command>."""

import argparse
import json
import secrets
import sys
from pathlib import Path


def cmd_setup_env(args) -> int:
    env = Path(".env")
    if env.exists() and not args.force:
        print(".env already exists (use --force to overwrite)")
        return 1
    text = Path(".env.example").read_text()
    for key in ("POSTGRES_PASSWORD", "SESSION_SECRET", "ADMIN_PASSWORD"):
        size = 48 if key == "SESSION_SECRET" else 18
        text = text.replace(f"{key}=change-me", f"{key}={secrets.token_urlsafe(size)}")
    env.write_text(text)
    env.chmod(0o600)
    print("wrote .env with random secrets (mode 600); the admin password is ADMIN_PASSWORD in .env")
    return 0


def cmd_generate(args) -> int:
    from p7.config import get_settings
    from p7.generate.files import generate, write_sample_zip

    s = get_settings()
    out = Path(args.out or s.data_dir / "synthetic")
    manifest = generate(out, args.seed if args.seed is not None else s.gen_seed)
    t = manifest["totals"]
    print(f"wrote {t['files']} files, {t['data_rows']:,} data rows -> {out}/xlsx (SYNTHETIC)")
    print(f"expected readings {t['expected_readings']:,}; planted rejects {json.dumps(t['expected_rejects'])}")
    if args.sample_month:
        z = write_sample_zip(out, args.sample_month, Path("data/sample") / f"sensors_{args.sample_month}.zip")
        print(f"sample zip -> {z}")
    return 0


def cmd_migrate(args) -> int:
    from p7.db import connect, migrate
    from p7.sites import seed_sites

    with connect() as conn:
        applied = migrate(conn)
        n = seed_sites(conn)
    print(f"migrations applied: {applied or 'none (up to date)'}; views refreshed; sites inserted: {n}")
    return 0


def cmd_reset_db(args) -> int:
    from p7.db import connect, reset

    if not args.yes:
        print("refusing to drop everything without --yes")
        return 1
    with connect() as conn:
        reset(conn)
    print("database reset (public schema dropped); run `python -m p7 migrate`")
    return 0


def cmd_create_admin(args) -> int:
    from p7.auth import create_user, get_user_by_name
    from p7.config import get_settings
    from p7.db import connect

    s = get_settings()
    if not s.admin_password or s.admin_password == "change-me":
        print("ADMIN_PASSWORD is not set in .env (run `python -m p7 setup-env`)")
        return 1
    with connect() as conn:
        if get_user_by_name(conn, s.admin_username):
            print(f"admin user '{s.admin_username}' already exists")
            return 0
        create_user(conn, s.admin_username, s.admin_password, "admin")
    print(f"created admin user '{s.admin_username}'")
    return 0


def cmd_create_user(args) -> int:
    import getpass

    from p7.auth import create_user
    from p7.db import connect

    password = args.password or getpass.getpass(f"password for {args.username}: ")
    with connect() as conn:
        create_user(conn, args.username, password, args.role)
    print(f"created {args.role} user '{args.username}'")
    return 0


def cmd_ingest(args) -> int:
    from p7.db import connect
    from p7.ingest.pipeline import ingest_paths

    with connect() as conn:
        results = ingest_paths(conn, [Path(p) for p in args.paths], source="cli", user_id=None)
    bad = 0
    for r in results:
        print(f"{r.status:15s} {r.filename:40s} site={r.site_code or '-':7s} month={r.month or '-':8s} "
              f"ins={r.inserted:4d} upd={r.updated:4d} same={r.unchanged:4d} rej={r.rejected:3d} {r.error or ''}")
        bad += r.status in ("rejected_file", "failed")
    if not args.no_reports:
        from p7.reports.build import build_after_upload

        with connect() as conn:
            built = build_after_upload(conn, results)
        print(f"reports built: {len(built)}")
    return 1 if bad and args.strict else 0


def cmd_report(args) -> int:
    from p7.db import connect
    from p7.reports.build import build_site_month, build_summary_month

    with connect() as conn:
        langs = args.lang.split(",")
        out = []
        for lang in langs:
            if args.site:
                out.append(build_site_month(conn, args.site, args.month, lang))
            else:
                out.append(build_summary_month(conn, args.month, lang))
    for p in out:
        print(p)
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from p7.config import get_settings

    s = get_settings()
    uvicorn.run("p7.web.app:create_app", factory=True, host=s.app_host, port=s.app_port,
                proxy_headers=False, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="p7", description="Multi-site sensor dashboard (SYNTHETIC data)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup-env", help="create .env from .env.example with random secrets")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_setup_env)

    s = sub.add_parser("generate", help="write the synthetic Excel files + manifest.json")
    s.add_argument("--seed", type=int)
    s.add_argument("--out")
    s.add_argument("--sample-month", help="also zip this month (YYYY-MM) into data/sample/")
    s.set_defaults(fn=cmd_generate)

    s = sub.add_parser("migrate", help="apply schema migrations, refresh views, seed the 13 sites")
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("reset-db", help="DROP all tables and views")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset_db)

    s = sub.add_parser("create-admin", help="create the first admin from ADMIN_USERNAME/ADMIN_PASSWORD")
    s.set_defaults(fn=cmd_create_admin)

    s = sub.add_parser("create-user", help="create a user")
    s.add_argument("username")
    s.add_argument("--role", choices=("admin", "management"), default="management")
    s.add_argument("--password", help="omit to be prompted")
    s.set_defaults(fn=cmd_create_user)

    s = sub.add_parser("ingest", help="ingest .xlsx files, folders or .zip archives")
    s.add_argument("paths", nargs="+")
    s.add_argument("--no-reports", action="store_true", help="skip PDF generation after loading")
    s.add_argument("--strict", action="store_true", help="exit 1 if any file is rejected")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("report", help="build a PDF: --site CODE --month YYYY-MM, or the all-sites summary")
    s.add_argument("--month", required=True)
    s.add_argument("--site")
    s.add_argument("--lang", default="en", help="en, ar or en,ar")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("serve", help="run the web app on APP_HOST:APP_PORT (127.0.0.1:8790)")
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
