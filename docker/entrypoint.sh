#!/bin/sh
# Apply migrations (idempotent), make sure the first admin exists, then run the given p7 command.
set -e
python -m p7 migrate
python -m p7 create-admin
exec python -m p7 "$@"
