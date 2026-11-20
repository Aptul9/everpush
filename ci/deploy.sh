#!/usr/bin/env bash
set -euo pipefail
umask 077

BASE=/opt/everpush
RELEASE=${1:?release SHA required}
[[ "$RELEASE" =~ ^[a-f0-9]{40}$ ]] || exit 1
cd "$BASE"
test -s release.tar.gz
test -s environment.new
test -s owner-password.new
install -d -m 755 -o 10001 -g 10001 data
install -d -m 755 releases
NEXT="$BASE/releases/$RELEASE"
install -d -m 755 "$NEXT"
tar xzf release.tar.gz -C "$NEXT"
chmod -R a+rX "$NEXT"

if test -e .env && ! cmp -s .env environment.new; then
  echo 'Deployment configuration changed; use a separately approved configuration change.' >&2
  exit 1
fi
if ! test -e .env; then mv environment.new .env; else rm environment.new; fi
chmod 600 .env
# EverID client settings and the password-login flag follow the repository on every deploy.
test -s oidc.env.new
mv oidc.env.new oidc.env
chmod 600 oidc.env
export RELEASE

compose() { docker compose --project-directory "$NEXT" --env-file "$BASE/.env" --env-file "$BASE/oidc.env" "$@"; }
compose config --quiet
compose build --pull
if test -L app; then
  readlink -f app > previous-release
  # SQLite online backup, not a live copy of the database file.
  python3 - <<'PY'
import sqlite3
from pathlib import Path
root = Path('/opt/everpush')
with sqlite3.connect(root / 'data/push.sqlite3') as source, sqlite3.connect(root / 'database.pre-deploy.sqlite3') as target:
    source.backup(target)
PY
fi
compose run --rm --no-deps app python manage.py migrate --noinput
compose run --rm --no-deps -T app python manage.py bootstrap_owner < owner-password.new
rm owner-password.new
compose run --rm --no-deps app python manage.py cleanup
printf '%s\n' "$NEXT" > candidate-release

python3 "$NEXT/ci/routing.py" /etc/sslh.cfg "$BASE/sslh.candidate"
if ! cmp -s /etc/sslh.cfg "$BASE/sslh.candidate"; then
  if ! test -e /etc/sslh.cfg.pre-everpush; then
    cp -a /etc/sslh.cfg /etc/sslh.cfg.pre-everpush
  fi
  # Only add the new route; all existing routes must remain byte-for-byte present.
  install -m 644 "$BASE/sslh.candidate" /etc/sslh.cfg
  systemctl restart sslh
  systemctl is-active --quiet sslh
fi

compose up -d --wait --wait-timeout 120 --remove-orphans
ln -sfn "$NEXT" app.next
mv -Tf app.next app
compose ps
compose logs --tail 15 app

# The public check includes DNS, certificate and the actual application.
curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors --max-time 15 https://push.everbright.it/healthz/ | grep -q '"status": "ok"'
test "$(curl --silent --show-error -o /dev/null -w '%{http_code}' https://push.everbright.it/login/)" = 302
echo "Deployment complete: $RELEASE"
