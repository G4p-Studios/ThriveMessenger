#!/usr/bin/env bash
set -euo pipefail

# Pull latest repo changes and restart PM2 process only when server-side files changed.
#
# Environment variables:
#   REPO_DIR           Repo path on server (default: /home/${DEPLOY_USER}/apps/ThriveMessenger)
#   REMOTE             Git remote (default: origin)
#   BRANCH             Git branch (default: main)
#   PM2_APP_NAME       PM2 app name/id (default: thrive-server)
#   PM2_BIN            PM2 binary path/name (default: pm2)
#   PM2_RUN_USER       Optional Linux user owning PM2 process (default: empty)
#   PM2_HOME_PATH      Optional PM2 home path for target user (default: empty)
#   POST_UPDATE_CMD    Optional command to run after pull and before restart
#   FORCE_RESTART      1 to restart even when no matching files changed (default: 0)
#   DEPLOY_LOCK_FILE   Lock file path (default: /tmp/thrive-server-deploy.lock)
#   DEPLOY_TIMEOUT_SEC Timeout for restart command (default: 120)
#
# Exit codes:
#   0 success (including "no changes")
#   2 local repo has uncommitted changes
#   3 restart command failed

DEFAULT_DEPLOY_USER="${DEPLOY_USER:-$(whoami)}"
REPO_DIR="${REPO_DIR:-/home/${DEFAULT_DEPLOY_USER}/apps/ThriveMessenger}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
PM2_APP_NAME="${PM2_APP_NAME:-thrive-server}"
PM2_BIN="${PM2_BIN:-pm2}"
PM2_RUN_USER="${PM2_RUN_USER:-}"
PM2_HOME_PATH="${PM2_HOME_PATH:-}"
POST_UPDATE_CMD="${POST_UPDATE_CMD:-}"
FORCE_RESTART="${FORCE_RESTART:-0}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/thrive-server-deploy.lock}"
DEPLOY_TIMEOUT_SEC="${DEPLOY_TIMEOUT_SEC:-120}"

restart_pm2() {
  local cmd="${PM2_BIN} restart ${PM2_APP_NAME} --update-env"
  if [[ -n "${PM2_HOME_PATH}" ]]; then
    cmd="PM2_HOME='${PM2_HOME_PATH}' ${cmd}"
  fi

  if [[ -n "${PM2_RUN_USER}" ]]; then
    timeout "${DEPLOY_TIMEOUT_SEC}" sudo -u "${PM2_RUN_USER}" bash -lc "${cmd}"
  else
    timeout "${DEPLOY_TIMEOUT_SEC}" bash -lc "${cmd}"
  fi
}

printf '[deploy] repo=%s remote=%s branch=%s pm2_app=%s\n' "${REPO_DIR}" "${REMOTE}" "${BRANCH}" "${PM2_APP_NAME}"

exec 9>"${DEPLOY_LOCK_FILE}"
flock -n 9 || {
  echo "[deploy] another deployment is running" >&2
  exit 0
}

cd "${REPO_DIR}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[deploy] local repo has uncommitted changes; aborting" >&2
  exit 2
fi

git fetch "${REMOTE}" "${BRANCH}" --prune

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse "${REMOTE}/${BRANCH}")"

if [[ "${local_sha}" == "${remote_sha}" ]]; then
  echo "[deploy] repository already at latest commit (${local_sha})"
  if [[ "${FORCE_RESTART}" == "1" ]]; then
    echo "[deploy] FORCE_RESTART=1, restarting process"
    restart_pm2 || { echo "[deploy] restart failed" >&2; exit 3; }
    echo "[deploy] restart completed"
  fi
  exit 0
fi

git pull --ff-only "${REMOTE}" "${BRANCH}"

new_sha="$(git rev-parse HEAD)"
changed_files="$(git diff --name-only "${local_sha}" "${new_sha}" || true)"

echo "[deploy] updated ${local_sha} -> ${new_sha}"

# Restart only if server/runtime-relevant files changed.
restart_needed=0
if [[ "${FORCE_RESTART}" == "1" ]]; then
  restart_needed=1
elif echo "${changed_files}" | rg -q '^(srv/|server\.conf|client\.conf|pyproject\.toml|requirements.*\.txt|scripts/|main\.py)'; then
  restart_needed=1
fi

if [[ -n "${POST_UPDATE_CMD}" ]]; then
  echo "[deploy] running post-update command"
  bash -lc "${POST_UPDATE_CMD}"
fi

if [[ "${restart_needed}" == "1" ]]; then
  echo "[deploy] restarting pm2 process ${PM2_APP_NAME}"
  restart_pm2 || { echo "[deploy] restart failed" >&2; exit 3; }
  echo "[deploy] restart completed"
else
  echo "[deploy] no server-relevant changes detected; restart skipped"
fi
