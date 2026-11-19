#!/usr/bin/env bash
set -euo pipefail
BASE=/opt/everpush
ENV_FILES=(--env-file "$BASE/.env")
if test -e "$BASE/oidc.env"; then ENV_FILES+=(--env-file "$BASE/oidc.env"); fi
if test "${1:-previous}" = disable; then
  if test -L "$BASE/app"; then CURRENT=$(readlink -f "$BASE/app"); else CURRENT=$(cat "$BASE/candidate-release"); fi
  case "$CURRENT" in "$BASE"/releases/*) ;; *) exit 1 ;; esac
  export RELEASE=${CURRENT##*/}
  docker compose --project-directory "$CURRENT" "${ENV_FILES[@]}" stop web app
  test -s /etc/sslh.cfg.pre-everpush
  cp /etc/sslh.cfg.pre-everpush /etc/sslh.cfg
  systemctl restart sslh
  systemctl is-active --quiet sslh
  echo 'EverPush disabled; data preserved'
  exit 0
fi
test -s "$BASE/previous-release"
PREVIOUS=$(cat "$BASE/previous-release")
case "$PREVIOUS" in "$BASE"/releases/*) ;; *) exit 1 ;; esac
export RELEASE=${PREVIOUS##*/}
docker compose --project-directory "$PREVIOUS" "${ENV_FILES[@]}" up -d --wait --wait-timeout 120 --remove-orphans
ln -sfn "$PREVIOUS" "$BASE/app.next"
mv -Tf "$BASE/app.next" "$BASE/app"
# Database and stored files stay in place. No destructive data restore.
curl --fail --silent --show-error https://push.everbright.it/healthz/
