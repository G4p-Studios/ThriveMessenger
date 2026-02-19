# Upstream-Safe PR Chunks

This branch includes deployment-specific defaults for Raywonder infrastructure.
For upstream submission, split into these chunks:

## Chunk 1: Client resource and macOS runtime reliability
- Resource path resolution for frozen/bundled app (`_resolve_existing_file`, `_resolve_existing_dir`).
- Sound playback fallback on macOS when `wx.adv.Sound` fails (`afplay` fallback).
- Directory payload coercion (`user` vs `username`) to avoid empty list rows.

## Chunk 2: Accessibility and usability
- Explicit `SetHelpText` on new login and directory controls.
- Directory server filter UX (`All Servers` and specific server filter).
- Update notification behavior improvements (silent checks notify, failures notify).

## Chunk 3: Multi-server account/session support
- Per-server account storage (`server_accounts`) with keyring-backed passwords.
- Optional multi-server sign-in flag.
- Secondary concurrent sessions for additional configured servers.
- Per-chat server routing for send/typing and server-scoped chat windows.

## Chunk 4: Optional updater source abstraction (generic-safe)
- Keep updater fallback to GitHub repos.
- Keep update source configurable from `client.conf`.
- For upstream, default `preferred_repo` to `G4p-Studios/ThriveMessenger`.

## Keep only in fork (do not upstream)
- `feed_url` defaults targeting custom domains.
- `srv/scripts/sync_update_feed.sh` if upstream does not want server-side release sync scripts.
- Documentation examples referencing `im.tappedin.fm`.

