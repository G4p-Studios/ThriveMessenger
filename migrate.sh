#!/bin/bash
# Migrate Thrive Messenger from legacy server.py to Prosody XMPP.
#
# Run from the ThriveMessenger repo root on the Prosody server as root.
#
# Usage:
#   bash migrate.sh                  # Full migration
#   bash migrate.sh -d               # Dry run (preview only)
#   bash migrate.sh -c               # Contacts only (accounts already migrated)
#   bash migrate.sh -r MANIFEST      # Rollback using a manifest file
#
# The script auto-detects the XMPP domain from prosody.cfg.lua and locates
# the legacy thrive.db in the repo.  Override with environment variables:
#   DOMAIN=example.com OLD_DB=/path/to/thrive.db bash migrate.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Parse flags ---
DRY_RUN=""
CONTACTS_ONLY=""
ROLLBACK=""

while getopts "dcr:" opt; do
    case "$opt" in
        d) DRY_RUN="--dry-run" ;;
        c) CONTACTS_ONLY="--contacts-only" ;;
        r) ROLLBACK="$OPTARG" ;;
        *) echo "Usage: bash migrate.sh [-d] [-c] [-r MANIFEST]"; exit 1 ;;
    esac
done

# --- Rollback mode ---
if [ -n "$ROLLBACK" ]; then
    echo "Rolling back migration using: $ROLLBACK"
    python3 "$SCRIPT_DIR/srv/migrate_accounts.py" --rollback "$ROLLBACK"
    exit $?
fi

# --- Auto-detect domain from Prosody config ---
if [ -z "$DOMAIN" ]; then
    DOMAIN=$(grep -oP 'VirtualHost\s+"?\K[^"]+' /etc/prosody/prosody.cfg.lua 2>/dev/null | head -1)
fi
if [ -z "$DOMAIN" ]; then
    echo "Error: Could not detect XMPP domain from /etc/prosody/prosody.cfg.lua"
    echo "Set it manually:  DOMAIN=example.com bash migrate.sh"
    exit 1
fi

# --- Locate legacy database ---
OLD_DB="${OLD_DB:-$SCRIPT_DIR/srv/thrive.db}"
if [ ! -f "$OLD_DB" ]; then
    echo "Error: Legacy database not found at $OLD_DB"
    echo "Set it manually:  OLD_DB=/path/to/thrive.db bash migrate.sh"
    exit 1
fi

# --- Paths ---
PROSODY_DB="/var/lib/prosody/thrive.db"
PROSODY_DATA="/var/lib/prosody"
ADMINS_FILE="$SCRIPT_DIR/srv/admins.txt"

echo "=== Thrive Messenger Migration ==="
echo "  Domain:       $DOMAIN"
echo "  Legacy DB:    $OLD_DB"
echo "  Prosody DB:   $PROSODY_DB"
echo "  Prosody data: $PROSODY_DATA"
if [ -n "$DRY_RUN" ]; then
    echo "  Mode:         DRY RUN (no changes)"
elif [ -n "$CONTACTS_ONLY" ]; then
    echo "  Mode:         Contacts only"
else
    echo "  Mode:         Full migration"
fi
echo ""

# --- Run migration ---
python3 "$SCRIPT_DIR/srv/migrate_accounts.py" \
    --old-db "$OLD_DB" \
    --prosody-db "$PROSODY_DB" \
    --prosody-data "$PROSODY_DATA" \
    --domain "$DOMAIN" \
    --admins-file "$ADMINS_FILE" \
    $DRY_RUN $CONTACTS_ONLY

# --- Post-migration: fix file ownership ---
if [ -z "$DRY_RUN" ]; then
    echo ""
    echo "=== Fixing Prosody file ownership ==="
    chown -R prosody:prosody "$PROSODY_DATA"
    echo "  Done."
    echo ""
    echo "Next steps:"
    echo "  1. Deploy Thrive modules:  bash prosody/deploy_modules.sh"
    echo "  2. Restart Prosody:        systemctl restart prosody"
fi
