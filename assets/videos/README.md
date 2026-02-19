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

## Long clips (external provider)

For longer edits, use another service (for example Runway/Pika/Kling), then place final outputs under:
- `assets/videos/long/`

Keep filenames stable so installers and docs do not break.
