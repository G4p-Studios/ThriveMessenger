#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEO_DIR="${ROOT_DIR}/assets/videos"
WORK_DIR="${TMPDIR:-/tmp}/thrive_narration_work"
VOICE="${VOICE:-Alex}"
RATE="${RATE:-185}"

mkdir -p "${WORK_DIR}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required." >&2
  exit 1
fi
if ! command -v say >/dev/null 2>&1; then
  echo "macOS 'say' command is required for narration generation." >&2
  exit 1
fi

desc_for() {
  case "$1" in
    promo-onboarding.mp4)
      echo "Onboarding demo. This clip shows launching Thrive Messenger, signing in, and arriving on your contact list."
      ;;
    promo-chat-files.mp4)
      echo "Chat and file demo. This clip shows selecting a contact, sending a message, and sending files with transfer confirmation."
      ;;
    promo-admin-tools.mp4)
      echo "Admin tools demo. This clip shows opening Server Manager, reviewing multiple servers, and updating primary server settings."
      ;;
    *)
      echo ""
      ;;
  esac
}

format_srt_time() {
  python3 - "$1" <<'PY'
import sys
t = float(sys.argv[1])
h = int(t // 3600)
m = int((t % 3600) // 60)
s = int(t % 60)
ms = int(round((t - int(t)) * 1000))
print(f"{h:02d}:{m:02d}:{s:02d},{ms:03d}")
PY
}

for video_path in "${VIDEO_DIR}"/promo-*.mp4; do
  base="$(basename "${video_path}")"
  desc="$(desc_for "${base}")"
  if [[ -z "${desc}" ]]; then
    echo "Skipping ${base}: no description configured."
    continue
  fi

  duration="$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "${video_path}")"
  end_time="$(python3 - "$duration" <<'PY'
import sys
d=max(0.5,float(sys.argv[1])-0.2)
print(d)
PY
)"
  srt_start="00:00:00,500"
  srt_end="$(format_srt_time "${end_time}")"

  narration_aiff="${WORK_DIR}/${base%.mp4}.aiff"
  subtitle_srt="${WORK_DIR}/${base%.mp4}.srt"
  out_tmp="${WORK_DIR}/${base%.mp4}.described.tmp.mp4"

  say -v "${VOICE}" -r "${RATE}" -o "${narration_aiff}" "${desc}"

  cat > "${subtitle_srt}" <<EOF
1
${srt_start} --> ${srt_end}
${desc}
EOF

  ffmpeg -y \
    -i "${video_path}" \
    -i "${narration_aiff}" \
    -i "${subtitle_srt}" \
    -filter_complex "[0:a]volume=0.30[bg];[1:a]volume=1.20[vo];[bg][vo]amix=inputs=2:duration=first:normalize=0[aout]" \
    -map 0:v:0 \
    -map "[aout]" \
    -map 2:0 \
    -c:v copy \
    -c:a aac -b:a 160k \
    -c:s mov_text \
    -metadata:s:s:0 language=eng \
    "${out_tmp}"

  mv "${out_tmp}" "${video_path}"
  echo "Described + narrated: ${base}"
done

echo "Done. Updated videos in ${VIDEO_DIR}"
