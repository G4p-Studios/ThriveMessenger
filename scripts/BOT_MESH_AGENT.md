# Thrive Bot Mesh Agent

`thrive_bot_mesh_agent.py` lets a bot account connect to a Thrive Messenger server without the desktop UI, register into the bot-mesh directory, answer delegated work, and optionally watch moderation events.

## Example

```bash
python3 scripts/thrive_bot_mesh_agent.py \
  --host im.tappedin.fm \
  --port 2005 \
  --ssl \
  --user codex-bot \
  --password 'your-password' \
  --backend ollama \
  --auth-type codex \
  --moderation \
  --notify-user admin \
  --host-label devmac \
  --background
```

## Useful modes

- `--backend ollama`: use a local or remote Ollama server for replies and moderation summaries.
- `--backend command --command "..."`: bridge to another local CLI agent.
- `--delegate-to helper-bot`: forward work to another connected bot when the local backend fails.
- `--accept-files`: allow bot-mesh file staging/fetch into the bot temp directory.

## Moderation

When `--moderation` is enabled, the agent can receive:

- `guest_login`
- `direct_message`
- `file_offer`

If `--notify-user` is set, the agent will send moderation summaries back into Thrive as normal bot messages for review.
