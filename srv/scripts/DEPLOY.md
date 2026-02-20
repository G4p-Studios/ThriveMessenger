# Server Deploy + Restart Automation

This repo now includes:

- `srv/scripts/deploy_and_restart.sh`
- `srv/scripts/deploy_hook_api.py`
- `.github/workflows/deploy-server.yml`

## 1) One-time server setup

1. Ensure repo is present on server (example):
   - `/opt/thrive/ThriveMessenger`
2. Ensure PM2 process exists (example app name):
   - `thrive-server`
3. If deploy user is not PM2 owner, allow targeted sudo for PM2 restart.

Example sudoers snippet:

```sudoers
# Allow deploy user to restart only one PM2 process as the PM2 owner.
Cmnd_Alias THRIVE_PM2 = /usr/bin/bash -lc PM2_HOME=* pm2 restart thrive-server --update-env
<deploy-user> ALL=(<pm2-owner>) NOPASSWD: THRIVE_PM2
```

Adjust names and command path for your system.

## 2) Test deploy script manually

```bash
REPO_DIR=/opt/thrive/ThriveMessenger \
PM2_APP_NAME=thrive-server \
PM2_RUN_USER=thrive \
PM2_HOME_PATH=/home/thrive/.pm2 \
bash /opt/thrive/ThriveMessenger/srv/scripts/deploy_and_restart.sh
```

## 3) GitHub Actions deploy over SSH

Configure repo secrets:

- `DEPLOY_SSH_KEY`
- `DEPLOY_HOST`
- `DEPLOY_PORT` (optional)
- `DEPLOY_USER`
- `DEPLOY_REPO_DIR`
- `DEPLOY_PM2_APP_NAME`
- `DEPLOY_PM2_RUN_USER` (optional)
- `DEPLOY_PM2_HOME` (optional)
- `DEPLOY_KNOWN_HOSTS` (optional)

Then run workflow `Deploy Server` (manual) or push matching paths to `main`.

## 4) Optional local API endpoint

Run API endpoint on server (bind localhost):

```bash
export DEPLOY_API_TOKEN='strong-random-token'
export REPO_DIR='/opt/thrive/ThriveMessenger'
export PM2_APP_NAME='thrive-server'
export PM2_RUN_USER='thrive'
export PM2_HOME_PATH='/home/thrive/.pm2'
python3 /opt/thrive/ThriveMessenger/srv/scripts/deploy_hook_api.py
```

Trigger deploy:

```bash
curl -sS -X POST http://127.0.0.1:18777/deploy \
  -H "Authorization: Bearer ${DEPLOY_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"force_restart":false}'
```

Recommended: expose only behind reverse proxy + auth (Authelia) or keep localhost-only.
