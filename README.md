# EverPush

Personal clipboard between devices at `https://push.everbright.it`. One owner account, `aptul4ever@gmail.com`. No registration, invitations or account-management interface.

Text and files sent from one browser appear in every other open session within about two seconds. Each page polls `/api/items/?after=<last id>` every 2 seconds while visible and every 15 seconds while hidden; the response carries all current ids, so deletions propagate as well.

## Limits

- 300 items. Each new item deletes the oldest ones beyond the limit, file bytes included.
- Files up to 10 MiB. Larger files are rejected with a link to EverDrive, `https://drive.everbright.it`.
- Text up to 100,000 characters.
- Uploads stop when free disk space would fall below the 20 GB VM reserve.

Images (PNG, JPEG, GIF, WebP) are previewed and can be copied to the clipboard as PNG. Every other file is served only as an attachment, under a sandbox CSP.

## Deployment

`.github/workflows/ci-cd.yml` tests Python behavior, routing transformations and production containers. Pull requests run checks. Main deploys through SSH using `ci/deploy.sh`: release directory, SQLite migration, owner creation when absent, orphan file cleanup, sslh route `push.everbright.it` to `127.0.0.1:4448`, Compose start and public checks.

Required repository secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DJANGO_SECRET_KEY`, `OWNER_PASSWORD`. Existing owner password is never reset by deployment. Runtime files live outside release directories in `/opt/everpush/data`.

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
