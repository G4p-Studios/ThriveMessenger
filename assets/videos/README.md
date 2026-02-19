# Promo Videos

This folder stores app promo/demo videos that can be opened from:
- `Help -> Open Demo Videos Folder` in Thrive Messenger.

## Short clips (Sora 2)

Generate short clips with:

```bash
export OPENAI_API_KEY=...
./scripts/generate_sora_promos.sh
```

Output files:
- `promo-onboarding.mp4`
- `promo-chat-files.mp4`
- `promo-admin-tools.mp4`

## Local/offline clips (no cloud)

If Sora/API is unavailable, generate local promo clips with FFmpeg:

```bash
./scripts/generate_local_promos.sh
```

Notes:
- Works fully offline with `ffmpeg`.
- If local Ollama is available, it will try to produce short beat lines.
- Output filenames stay the same as Sora flow.

## Long clips (external provider)

For longer edits, use another service (for example Runway/Pika/Kling), then place final outputs under:
- `assets/videos/long/`

Keep filenames stable so installers and docs do not break.

Quick import helper:

```bash
./scripts/import_external_promos.sh /path/to/clip1.mp4 /path/to/clip2.mp4
```

Recommended standard names:
- `promo-onboarding.mp4`
- `promo-chat-files.mp4`
- `promo-admin-tools.mp4`
