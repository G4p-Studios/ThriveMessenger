#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sync_update_feed.sh [owner/repo] [output_json_path]
# Example:
#   sync_update_feed.sh Raywonder/ThriveMessenger /var/www/im.tappedin.fm/updates/latest.json

PRIMARY_REPO="${1:-Raywonder/ThriveMessenger}"
OUT_PATH="${2:-/var/www/im.tappedin.fm/updates/latest.json}"
FALLBACK_REPOS="${THRIVE_UPDATE_FALLBACK_REPOS:-G4p-Studios/ThriveMessenger}"

TMP_JSON="$(mktemp)"
TMP_OUT="$(mktemp)"

cleanup() {
  rm -f "${TMP_JSON}" "${TMP_OUT}"
}
trap cleanup EXIT

SELECTED_REPO=""
for REPO in "${PRIMARY_REPO}" ${FALLBACK_REPOS}; do
  API_URL="https://api.github.com/repos/${REPO}/releases/latest"
  if curl -fsSL "${API_URL}" -o "${TMP_JSON}"; then
    SELECTED_REPO="${REPO}"
    break
  fi
done

if [[ -z "${SELECTED_REPO}" ]]; then
  echo "Failed to fetch release metadata from primary/fallback repos." >&2
  exit 1
fi

python3 -c '
import json, sys
src, dst, repo = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)
assets = data.get("assets", [])
def find(name):
    for a in assets:
        if a.get("name") == name:
            return a.get("browser_download_url")
    return ""
out = {
    "repo": repo,
    "tag": data.get("tag_name", ""),
    "published_at": data.get("published_at", ""),
    "zip_url": find("thrive_messenger.zip"),
    "installer_url": find("thrive_messenger_installer.exe"),
    "notes_url": data.get("html_url", "")
}
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)
' "${TMP_JSON}" "${TMP_OUT}" "${SELECTED_REPO}"

mkdir -p "$(dirname "${OUT_PATH}")"
mv "${TMP_OUT}" "${OUT_PATH}"
chmod 0644 "${OUT_PATH}"
echo "Wrote ${OUT_PATH} from ${SELECTED_REPO}"
