#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "[1/5] Upstream parity report"
./scripts/upstream_parity_report.py

echo "[2/5] Python syntax checks"
python3 -m py_compile main.py srv/server.py

echo "[3/5] Build macOS app"
./scripts/build_macos.sh

ZIP_PATH="${ROOT_DIR}/dist-macos/thrive_messenger-macos-x86_64.zip"
APP_DIR="${ROOT_DIR}/dist/Thrive Messenger.app"

echo "[4/5] Artifact existence checks"
test -f "${ZIP_PATH}"
test -d "${APP_DIR}"
test -f "${APP_DIR}/Contents/Resources/client.conf"

echo "[5/5] Sound pack/update feed sanity"
ZIP_LIST="$(mktemp)"
trap 'rm -f "${ZIP_LIST}"' EXIT
unzip -l "${ZIP_PATH}" > "${ZIP_LIST}"
grep -q "sounds/default/login.wav" "${ZIP_LIST}"
grep -q "sounds/galaxia/login.wav" "${ZIP_LIST}"
grep -q "sounds/skype/login.wav" "${ZIP_LIST}"
if grep -qE "^[[:space:]]*feed_url[[:space:]]*=[[:space:]]*https?://" client.conf; then
  FEED_URL="$(grep -E "^[[:space:]]*feed_url[[:space:]]*=" client.conf | head -n1 | cut -d= -f2- | xargs)"
  echo "Checking configured feed_url: ${FEED_URL}"
  curl -fsSI "${FEED_URL}" >/dev/null
else
  echo "feed_url not set in client.conf (allowed)."
fi

echo "Release gate passed."
