#!/bin/bash
# Deploy updated Thrive Prosody modules and config to the test server.
# Run on the server as root, from the ThriveMessenger repo root.
#
# Usage:  bash prosody/deploy_modules.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULES_DIR="$SCRIPT_DIR/modules"

# Verify modules directory exists.
if [ ! -d "$MODULES_DIR" ]; then
    echo "ERROR: modules directory not found at $MODULES_DIR"
    echo "  SCRIPT_DIR resolved to: $SCRIPT_DIR"
    echo "  Run this script from the repo root: bash prosody/deploy_modules.sh"
    exit 1
fi

echo "=== Deploying Thrive modules (from $MODULES_DIR) ==="
mkdir -p /etc/prosody/thrive-modules
cp "$MODULES_DIR"/*.lua /etc/prosody/thrive-modules/
cp "$MODULES_DIR"/verify_argon2.py /etc/prosody/thrive-modules/
chmod +x /etc/prosody/thrive-modules/verify_argon2.py
chown -R prosody:prosody /etc/prosody/thrive-modules
echo "  Deployed $(ls "$MODULES_DIR"/*.lua | wc -l) Lua modules + verify_argon2.py"

echo "=== Deploying Prosody config ==="
cp "$SCRIPT_DIR"/prosody.cfg.lua.testserver /etc/prosody/prosody.cfg.lua
chown prosody:prosody /etc/prosody/prosody.cfg.lua
echo "  Done."

echo "=== Clearing old error log ==="
> /var/log/prosody/prosody.err
echo "  Done."

echo "=== Restarting Prosody ==="
systemctl restart prosody
sleep 2

echo "=== Prosody error log ==="
if [ -s /var/log/prosody/prosody.err ]; then
    cat /var/log/prosody/prosody.err
else
    echo "  (empty — no errors)"
fi

echo ""
echo "=== Recent Prosody log ==="
tail -20 /var/log/prosody/prosody.log

echo ""
echo "=== Prosody status ==="
systemctl status prosody --no-pager -l
