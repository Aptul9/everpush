# EverPush

Personal clipboard between devices at `https://push.everbright.it`. Login through EverID; every user with access sees and manages the same shared data.

Text and files sent from one browser appear in every other open session within about two seconds. Each page polls `/api/items/?after=<last id>` every 2 seconds while visible and every 15 seconds while hidden; the response carries all current ids, so deletions propagate as well.

## Login

EverID (Authentik at `id.everbright.it`) is the primary login: OIDC authorization code with PKCE, client `everpush`, callback `/oidc/callback`, scopes `openid email profile`. Access requires group `app-push` in the `groups` claim; other EverID users get HTTP 403 and no session. Users are matched by email (case-insensitive) or created on first login without a usable password. Logout ends the local session and redirects to the EverID end-session endpoint with `id_token_hint`.

Unauthenticated access first requests silent OIDC authentication. When interaction is required, the browser goes to the central login page. Logout suppresses silent authentication until the user chooses to sign in again. The application does not accept a credential form. `LEGACY_LOGIN=0` invalidates existing legacy sessions; startup refuses that setting without OIDC client credentials.

## Limits

- 300 items. Each new item deletes the oldest ones beyond the limit, file bytes included.
- Files up to 10 MiB. Larger files are rejected with a link to EverDrive, `https://drive.everbright.it`.
- Text up to 100,000 characters.
- Uploads stop when free disk space would fall below the 20 GB VM reserve.

Images (PNG, JPEG, GIF, WebP) are previewed and can be copied to the clipboard as PNG. Every other file is served only as an attachment, under a sandbox CSP.

## Deployment

`.github/workflows/ci-cd.yml` tests Python behavior, routing transformations and production containers. Pull requests run checks. Main deploys through SSH using `ci/deploy.sh`: release directory, SQLite migration, owner creation when absent, orphan file cleanup, sslh route `push.everbright.it` to `127.0.0.1:4448`, Compose start and public checks.

Required repository secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DJANGO_SECRET_KEY`, `OWNER_PASSWORD`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`; variables `OIDC_ISSUER`, optional `LEGACY_LOGIN`. Deploy stops when the EverID client is missing. EverID settings are rewritten to `oidc.env` on every deploy. Existing owner password is never reset by deployment. Runtime files live outside release directories in `/opt/everpush/data`.

## Rollback

The Rollback workflow runs `ci/rollback.sh`. Operation `previous` starts the recorded previous release on the same data. Operation `disable` stops the application and restores `/etc/sslh.cfg.pre-everpush`. No data is deleted.

## Local checks

Set `DJANGO_SECRET_KEY`, `PUSH_DATA_DIR`, `PUSH_ALLOWED_HOSTS=localhost,127.0.0.1,testserver` and `PUSH_INSECURE_LOCAL=1`. Create the data directory, then run:

```sh
python -m pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py bootstrap_owner < owner-password.txt
python manage.py test push
python -m unittest discover -s ci -p 'test_*.py'
python manage.py runserver 127.0.0.1:8793 --noreload --insecure
```

## Traps

- Browsers submit form text with CRLF line endings; the server stores LF.
- Each sslh `disable` rollback restores the configuration saved before that application's first route. Disabling EverDrive after EverPush was deployed, or the reverse, can drop the other route: check `/etc/sslh.cfg` afterwards.
- Same-origin form submissions require `Referrer-Policy: same-origin`. Do not enable `no-referrer` globally or whitelist `Origin: null`.
