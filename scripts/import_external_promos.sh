#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${ROOT_DIR}/assets/videos"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/video1.mp4 [/path/to/video2.mp4 ...]" >&2
  echo "Copies external provider renders into ${DEST_DIR}" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}" "${DEST_DIR}/long"

for src in "$@"; do
  if [[ ! -f "${src}" ]]; then
    echo "Skipping missing file: ${src}" >&2
    continue
  fi
  base="$(basename "${src}")"
  cp -f "${src}" "${DEST_DIR}/${base}"
  echo "Imported ${base} -> ${DEST_DIR}/${base}"
done

echo "Import complete."
echo "Recommended standard names:"
echo "  promo-onboarding.mp4"
echo "  promo-chat-files.mp4"
echo "  promo-admin-tools.mp4"
