#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPTS_DIR="${ROOT_DIR}/assets/videos/prompts"
OUT_DIR="${ROOT_DIR}/assets/videos"
FPS="${FPS:-30}"
SECONDS="${LOCAL_PROMO_SECONDS:-10}"
SIZE="${LOCAL_PROMO_SIZE:-1280x720}"
FONT_FILE="${LOCAL_PROMO_FONT:-/System/Library/Fonts/Supplemental/Arial.ttf}"

mkdir -p "${OUT_DIR}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but not installed." >&2
  exit 1
fi

HAVE_DRAWTEXT="0"
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q " drawtext "; then
  HAVE_DRAWTEXT="1"
fi

W="${SIZE%x*}"
H="${SIZE#*x}"

safe_text() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//:/\\:}"
  s="${s//\'/\\\'}"
  s="${s//,/\\,}"
  s="${s//%/\\%}"
  echo "$s"
}

build_beats() {
  local prompt_file="$1"
  local title="$2"
  local beats=""
  if command -v ollama >/dev/null 2>&1; then
    set +e
    beats="$(OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}" ollama run llama3.2 "Create 3 short promo beats (one line each) for: ${title}. Keep each line under 48 characters." 2>/dev/null | sed '/^[[:space:]]*$/d' | head -n 3)"
    set -e
  fi
  if [[ -z "${beats}" ]]; then
    beats="$(grep -E '^(Primary request|Action|Timing/beats|Text \(verbatim\)):' "${prompt_file}" | cut -d: -f2- | sed 's/^ *//' | head -n 3)"
  fi
  if [[ -z "${beats}" ]]; then
    beats="Launch and connect
Chat and share files
Manage servers and settings"
  fi
  echo "${beats}"
}

render_clip() {
  local prompt_name="$1"
  local out_file="$2"
  local title="$3"
  local accent="$4"
  local prompt_path="${PROMPTS_DIR}/${prompt_name}"

  if [[ ! -f "${prompt_path}" ]]; then
    echo "Missing prompt file: ${prompt_path}" >&2
    return 1
  fi

  local beats beat1 beat2 beat3
  beats="$(build_beats "${prompt_path}" "${title}")"
  beat1="$(echo "${beats}" | sed -n '1p')"
  beat2="$(echo "${beats}" | sed -n '2p')"
  beat3="$(echo "${beats}" | sed -n '3p')"
  [[ -z "${beat1}" ]] && beat1="Accessible desktop messaging"
  [[ -z "${beat2}" ]] && beat2="Fast chat and file transfer"
  [[ -z "${beat3}" ]] && beat3="Admin-ready server controls"

  beat1="$(safe_text "${beat1}")"
  beat2="$(safe_text "${beat2}")"
  beat3="$(safe_text "${beat3}")"
  title="$(safe_text "${title}")"

  if [[ "${HAVE_DRAWTEXT}" == "1" ]]; then
    ffmpeg -y \
      -f lavfi -i "color=c=#101522:s=${SIZE}:r=${FPS}:d=${SECONDS}" \
      -f lavfi -i "color=c=${accent}:s=${SIZE}:r=${FPS}:d=${SECONDS}" \
      -filter_complex "\
[0:v]format=rgba[base];\
[1:v]format=rgba,colorchannelmixer=aa=0.10[accent];\
[base][accent]overlay=shortest=1[bg];\
[bg]drawbox=x=0:y=0:w=${W}:h=90:color=black@0.35:t=fill,\
drawtext=fontfile='${FONT_FILE}':text='Thrive Messenger':x=36:y=22:fontsize=42:fontcolor=white,\
drawtext=fontfile='${FONT_FILE}':text='${title}':x=36:y=66:fontsize=24:fontcolor=white@0.92,\
drawtext=fontfile='${FONT_FILE}':text='${beat1}':x=48:y='if(lt(t,3),${H}, ${H}-((t-0.6)*220))':fontsize=40:fontcolor=white,\
drawtext=fontfile='${FONT_FILE}':text='${beat2}':x=48:y='if(lt(t,5),${H}, ${H}-((t-2.6)*220))':fontsize=40:fontcolor=white,\
drawtext=fontfile='${FONT_FILE}':text='${beat3}':x=48:y='if(lt(t,7),${H}, ${H}-((t-4.6)*220))':fontsize=40:fontcolor=white,\
drawtext=fontfile='${FONT_FILE}':text='tappedin.fm':x='w-tw-36':y='h-th-24':fontsize=28:fontcolor=white@0.85" \
      -c:v libx264 -pix_fmt yuv420p -r "${FPS}" \
      "${OUT_DIR}/${out_file}"
  else
    ffmpeg -y \
      -f lavfi -i "color=c=#101522:s=${SIZE}:r=${FPS}:d=${SECONDS}" \
      -f lavfi -i "testsrc2=s=${SIZE}:r=${FPS}:d=${SECONDS}" \
      -filter_complex "\
[1:v]hue=s=0.25,curves=preset=lighter[pattern];\
[0:v][pattern]blend=all_mode='overlay':all_opacity=0.30[bg];\
[bg]drawbox=x='mod(t*120,${W})':y='${H}*0.15':w='${W}*0.18':h='${H}*0.08':color=${accent}@0.45:t=fill,\
drawbox=x='${W}-mod(t*150,${W})':y='${H}*0.62':w='${W}*0.24':h='${H}*0.10':color=white@0.18:t=fill,\
drawbox=x='${W}*0.08':y='${H}*0.84':w='${W}*0.84':h='${H}*0.03':color=white@0.10:t=fill" \
      -c:v libx264 -pix_fmt yuv420p -r "${FPS}" \
      "${OUT_DIR}/${out_file}"
  fi
}

render_clip "onboarding.txt" "promo-onboarding.mp4" "Onboarding" "#2D6AFF"
render_clip "chat-and-files.txt" "promo-chat-files.mp4" "Chat and Files" "#0BB783"
render_clip "admin-tools.txt" "promo-admin-tools.mp4" "Admin Tools" "#FF8D3A"

echo "Local promo clips generated in ${OUT_DIR}:"
echo "  promo-onboarding.mp4"
echo "  promo-chat-files.mp4"
echo "  promo-admin-tools.mp4"
