#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/assets/videos"
PROMPTS_DIR="${ROOT_DIR}/assets/videos/prompts"
MODEL="${SORA_MODEL:-sora-2}"
SECONDS="${SORA_SECONDS:-8}"
SIZE="${SORA_SIZE:-1280x720}"

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SORA_CLI="${SORA_CLI:-${CODEX_HOME}/skills/sora/scripts/sora.py}"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set. Export it and rerun." >&2
  exit 1
fi

if [[ ! -f "${SORA_CLI}" ]]; then
  echo "Sora CLI not found at: ${SORA_CLI}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}" "${PROMPTS_DIR}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
mkdir -p "${UV_CACHE_DIR}"

run_clip() {
  local prompt_file="$1"
  local out_file="$2"
  local log_file
  log_file="$(mktemp)"
  echo "Generating ${out_file} from ${prompt_file}..."
  if uv run --with openai python "${SORA_CLI}" create-and-poll \
    --model "${MODEL}" \
    --prompt-file "${PROMPTS_DIR}/${prompt_file}" \
    --no-augment \
    --size "${SIZE}" \
    --seconds "${SECONDS}" \
    --download \
    --variant video \
    --out "${OUT_DIR}/${out_file}" 2>&1 | tee "${log_file}"; then
    rm -f "${log_file}"
    return 0
  fi
  if grep -q "billing_hard_limit_reached" "${log_file}"; then
    echo "Sora billing limit reached. Use external provider fallback for now." >&2
    echo "Place exported clips into: ${OUT_DIR}" >&2
    echo "Expected files: promo-onboarding.mp4, promo-chat-files.mp4, promo-admin-tools.mp4" >&2
  fi
  rm -f "${log_file}"
  return 1
}

run_clip "onboarding.txt" "promo-onboarding.mp4"
run_clip "chat-and-files.txt" "promo-chat-files.mp4"
run_clip "admin-tools.txt" "promo-admin-tools.mp4"

echo "Done. Clips saved to ${OUT_DIR}"
echo "For longer clips: use Runway/Pika/Kling and place final renders in ${OUT_DIR}/long/"
