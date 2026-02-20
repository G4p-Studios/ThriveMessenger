#!/usr/bin/env bash
set -euo pipefail

# Create a tar.gz bundle of server-side files for handoff via shared storage.
#
# Usage:
#   make_server_bundle.sh [output_path]
#
# Example:
#   srv/scripts/make_server_bundle.sh /tmp/thrive-server-bundle.tar.gz

OUT_PATH="${1:-}"
if [[ -z "${OUT_PATH}" ]]; then
  echo "usage: $0 <output_path.tar.gz>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

mkdir -p "$(dirname "${OUT_PATH}")"

# Only include files the server/runtime side needs.
git archive \
  --format=tar.gz \
  --output="${OUT_PATH}" \
  HEAD \
  srv \
  main.py \
  client.conf \
  pyproject.toml

echo "${OUT_PATH}"
