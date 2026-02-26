#!/usr/bin/env python3
"""
Thrive Messenger — Account Migration Script

Migrates user accounts, contacts, bans, file bans, and admins from the
legacy custom server (srv/thrive.db) to Prosody XMPP.

Usage:
    python srv/migrate_accounts.py --old-db srv/thrive.db \\
           --prosody-db /var/lib/prosody/thrive.db \\
           --domain msg.thecubed.cc

    # Undo a migration using the manifest file:
    python srv/migrate_accounts.py --rollback migration-manifest-20260226-153012.json

What it does:
    1. Backs up the Prosody data directory and thrive.db before starting.
    2. Reads all verified users from the old SQLite database.
    3. Creates each user in Prosody via ``prosodyctl register``.
       - Users with argon2 password hashes: registered with a dummy
         password, and the original argon2 hash is stored in the
         ``thrive_legacy_passwords`` table.  The custom auth module
         (mod_auth_thrive) verifies against these hashes on first login
         and transparently re-hashes to SCRAM-SHA-1.
       - Users with legacy plaintext passwords: registered with the
         actual password (prosodyctl hashes it to SCRAM).
    4. Migrates email addresses to thrive_emails.
    5. Migrates contacts to XMPP roster by writing Prosody's internal
       storage files directly (does not use prosodyctl mod_roster).
    6. Migrates active bans to thrive_bans.
    7. Migrates file bans to thrive_file_bans.
    8. Migrates admins from admins.txt to thrive_admins.
    9. Writes a JSON manifest recording every action taken, so the
       migration can be rolled back with ``--rollback``.

Users keep their existing passwords — no reset required.

Requirements:
    - ``prosodyctl`` must be available on PATH (run on the Prosody server).
    - The Prosody thrive.db must be writable (same file referenced by
      thrive_db_path in prosody.cfg.lua).
    - ``authentication = "internal_hashed"`` during migration (the default).
      After migration, deploy the thrive modules, switch to
      ``authentication = "thrive"``, and restart Prosody.
"""

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    print(f"[migrate] {msg}")


def warn(msg):
    print(f"[migrate] WARNING: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# prosodyctl wrapper
# ---------------------------------------------------------------------------

def prosodyctl(*args):
    """Run a prosodyctl command.  Returns (success, stdout)."""
    try:
        result = subprocess.run(
            ["prosodyctl", *args],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        return False, "prosodyctl not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "prosodyctl timed out"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def open_old_db(path):
    """Open the legacy thrive.db (read-only)."""
    if not os.path.exists(path):
        print(f"Error: Old database not found: {path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_prosody_db(path):
    """Open (or create) the Prosody thrive.db for writing migration data."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Ensure all Thrive tables exist (same schemas as the Prosody modules).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS thrive_emails (
            username TEXT PRIMARY KEY,
            email    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS thrive_bans (
            username     TEXT PRIMARY KEY,
            banned_until TEXT NOT NULL,
            ban_reason   TEXT
        );
        CREATE TABLE IF NOT EXISTS thrive_file_bans (
            username   TEXT NOT NULL,
            file_type  TEXT NOT NULL,
            until_date TEXT,
            reason     TEXT,
            PRIMARY KEY (username, file_type)
        );
        CREATE TABLE IF NOT EXISTS thrive_admins (
            username TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS thrive_reset (
            username   TEXT PRIMARY KEY,
            code       TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS thrive_verify (
            username   TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            code       TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS thrive_legacy_passwords (
            username      TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def create_backup(prosody_db_path, prosody_data_dir, backup_dir):
    """Snapshot the Prosody thrive.db and data directory before migration.

    Returns the backup directory path, or None on failure.
    """
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"migration-backup-{timestamp}")

    try:
        os.makedirs(backup_path, exist_ok=True)

        # Back up the Prosody thrive.db (our custom tables).
        if os.path.exists(prosody_db_path):
            shutil.copy2(prosody_db_path, os.path.join(backup_path, "thrive.db"))
            # Also grab the WAL/SHM if present.
            for suffix in ("-wal", "-shm"):
                wal = prosody_db_path + suffix
                if os.path.exists(wal):
                    shutil.copy2(wal, os.path.join(backup_path, "thrive.db" + suffix))
            log(f"  Backed up thrive.db -> {backup_path}/thrive.db")

        # Back up Prosody's internal data directory (accounts, roster, etc.).
        if prosody_data_dir and os.path.isdir(prosody_data_dir):
            dest = os.path.join(backup_path, "prosody-data")
            shutil.copytree(prosody_data_dir, dest)
            log(f"  Backed up Prosody data -> {dest}")
        else:
            log("  Prosody data directory not found — skipping data backup.")
            log("  (Use --prosody-data to specify, e.g. /var/lib/prosody)")

        log(f"  Backup saved to: {backup_path}")
        return backup_path

    except OSError as e:
        warn(f"Backup failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Manifest (records every action for rollback)
# ---------------------------------------------------------------------------

class Manifest:
    """Records every migration action so it can be undone."""

    def __init__(self, domain, prosody_db_path):
        self.data = {
            "version": 1,
            "domain": domain,
            "prosody_db": prosody_db_path,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_at": None,
            "backup_path": None,
            "users_created": [],         # usernames registered via prosodyctl
            "legacy_hashes_stored": [],   # usernames with argon2 entries
            "emails_stored": [],          # usernames with email entries
            "contacts_added": [],         # [owner, [contacts]] per user
            "roster_files_written": [],   # absolute paths to roster .dat files
            "bans_added": [],             # usernames with ban entries
            "file_bans_added": [],        # [username, file_type] pairs
            "admins_added": [],           # usernames with admin entries
        }

    def save(self, path):
        self.data["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "w") as f:
            json.dump(self.data, f, indent=2)
        log(f"  Manifest saved to: {path}")


# ---------------------------------------------------------------------------
# Prosody internal storage helpers
# ---------------------------------------------------------------------------

def encode_prosody_path(s):
    """Encode a string for Prosody's internal file storage paths.

    Matches Prosody's util.datamanager encode() function: all non-alphanumeric
    characters are replaced with %xx hex encoding.
    E.g. "msg.thecubed.cc" -> "msg%2ethecubed%2ecc"
    """
    result = []
    for c in s:
        if c.isalnum():
            result.append(c)
        else:
            result.append(f"%{ord(c):02x}")
    return "".join(result)


def write_prosody_roster(username, domain, entries, prosody_data_dir):
    """Write a roster .dat file to Prosody's internal storage.

    Args:
        username: The local part of the JID (e.g. "alice").
        domain: The XMPP domain (e.g. "msg.thecubed.cc").
        entries: Dict of {jid: {"subscription": "both"/"to"/"from"/"none"}}.
        prosody_data_dir: Path to Prosody's data directory (e.g. /var/lib/prosody).

    Creates/overwrites: <data_dir>/<encoded_host>/roster/<encoded_user>.dat
    """
    encoded_host = encode_prosody_path(domain)
    encoded_user = encode_prosody_path(username)

    roster_dir = os.path.join(prosody_data_dir, encoded_host, "roster")
    os.makedirs(roster_dir, exist_ok=True)

    roster_path = os.path.join(roster_dir, f"{encoded_user}.dat")

    with open(roster_path, "w") as f:
        f.write("return {\n")
        # Roster metadata (tells Prosody to send full roster on first connect).
        f.write("\t[false] = {\n")
        f.write("\t\tversion = true;\n")
        f.write("\t};\n")
        # Contact entries.
        for jid in sorted(entries):
            sub = entries[jid].get("subscription", "both")
            f.write(f'\t["{jid}"] = {{\n')
            f.write(f'\t\tsubscription = "{sub}";\n')
            f.write("\t\tgroups = {\n")
            f.write("\t\t};\n")
            f.write("\t};\n")
        f.write("};\n")

    return roster_path


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

def migrate_users(old_db, prosody_db, domain, manifest, dry_run=False):
    """Create Prosody accounts for all verified users.

    For argon2-hashed passwords, the original hash is stored in
    thrive_legacy_passwords so mod_auth_thrive can verify on first login
    and transparently re-hash to SCRAM.  For legacy plaintext passwords,
    the actual password is passed to prosodyctl (which hashes it to SCRAM).

    Returns the number of users created.
    """
    rows = old_db.execute(
        "SELECT username, password, email, is_verified FROM users"
    ).fetchall()

    created = 0
    skipped = 0
    legacy_stored = 0

    for row in rows:
        username = row["username"].strip().lower()
        stored_password = row["password"] or ""
        email = (row["email"] or "").strip()
        verified = row["is_verified"]

        if not verified:
            log(f"  Skipping unverified user: {username}")
            skipped += 1
            continue

        if not username:
            continue

        # Check if user already exists in Prosody.
        ok, out = prosodyctl("user", "list", domain)
        if ok and username in out.split("\n"):
            log(f"  User already exists in Prosody: {username}")
            skipped += 1
            continue

        is_argon2 = stored_password.startswith("$argon2")

        # For argon2 hashes: register with a dummy password (the auth
        # module ignores it and checks the legacy hash instead).
        # For plaintext: register with the actual password.
        register_password = secrets.token_urlsafe(16) if is_argon2 else stored_password

        if dry_run:
            tag = "argon2" if is_argon2 else "plaintext"
            log(f"  [DRY RUN] Would create user: {username} ({tag})")
        else:
            ok, out = prosodyctl("register", username, domain, register_password)
            if ok:
                log(f"  Created user: {username}")
                created += 1
                manifest.data["users_created"].append(username)
            else:
                warn(f"  Failed to create user {username}: {out}")
                continue

            # Store legacy argon2 hash for mod_auth_thrive.
            if is_argon2:
                prosody_db.execute(
                    "INSERT OR REPLACE INTO thrive_legacy_passwords "
                    "(username, password_hash) VALUES (?, ?)",
                    (username, stored_password),
                )
                legacy_stored += 1
                manifest.data["legacy_hashes_stored"].append(username)

        # Migrate email to thrive_emails.
        if email:
            if dry_run:
                log(f"  [DRY RUN] Would store email for {username}: {email}")
            else:
                prosody_db.execute(
                    "INSERT OR REPLACE INTO thrive_emails (username, email) VALUES (?, ?)",
                    (username, email),
                )
                manifest.data["emails_stored"].append(username)

    prosody_db.commit()
    log(f"  Users: {created} created, {skipped} skipped, {legacy_stored} argon2 hashes stored")
    return created


def migrate_contacts(old_db, domain, prosody_data_dir, manifest, dry_run=False):
    """Migrate contacts to XMPP roster by writing Prosody internal storage files.

    Writes roster .dat files directly to Prosody's data directory, bypassing
    prosodyctl (which doesn't support mod_roster commands on Prosody 0.12.x).

    Detects bidirectional contact relationships and sets subscription="both"
    when both directions exist, or subscription="to" for one-way contacts.
    """
    rows = old_db.execute(
        "SELECT owner, contact, blocked FROM contacts"
    ).fetchall()

    # Build lookup structures.
    contact_pairs = set()       # (owner, contact) tuples for bidirectional check
    roster_map = {}             # owner -> {contact: {...}}

    for row in rows:
        owner = row["owner"].strip().lower()
        contact = row["contact"].strip().lower()

        if not owner or not contact:
            continue

        contact_pairs.add((owner, contact))
        roster_map.setdefault(owner, {})[contact] = {}

    # Write a roster file per user.
    added = 0
    errors = 0

    for owner, contacts in sorted(roster_map.items()):
        # Build roster entries with correct subscription state.
        entries = {}
        for contact in sorted(contacts):
            jid = f"{contact}@{domain}"
            is_mutual = (contact, owner) in contact_pairs
            entries[jid] = {"subscription": "both" if is_mutual else "to"}

        if dry_run:
            log(f"  [DRY RUN] Would write roster for {owner}: {len(entries)} entries")
            added += len(entries)
            continue

        try:
            path = write_prosody_roster(owner, domain, entries, prosody_data_dir)
            log(f"  Wrote roster for {owner}: {len(entries)} entries -> {path}")
            added += len(entries)
            manifest.data["contacts_added"].append([owner, list(contacts)])
            manifest.data.setdefault("roster_files_written", []).append(path)
        except OSError as e:
            warn(f"  Failed to write roster for {owner}: {e}")
            errors += len(contacts)

    log(f"  Contacts: {added} added, {errors} errors")
    if added and not dry_run:
        log("  NOTE: Run 'sudo chown -R prosody:prosody "
            f"{prosody_data_dir}' to fix file ownership.")
    return added


def migrate_bans(old_db, prosody_db, manifest, dry_run=False):
    """Migrate active user bans to thrive_bans."""
    today = time.strftime("%Y-%m-%d")
    rows = old_db.execute(
        "SELECT username, banned_until, ban_reason FROM users "
        "WHERE banned_until IS NOT NULL AND banned_until != ''"
    ).fetchall()

    migrated = 0
    expired = 0

    for row in rows:
        username = row["username"].strip().lower()
        banned_until = row["banned_until"]
        ban_reason = row["ban_reason"] or ""

        if not username or not banned_until:
            continue

        # Only migrate active bans.
        if banned_until < today:
            expired += 1
            continue

        if dry_run:
            log(f"  [DRY RUN] Would migrate ban: {username} until {banned_until}")
        else:
            prosody_db.execute(
                "INSERT OR REPLACE INTO thrive_bans (username, banned_until, ban_reason) VALUES (?, ?, ?)",
                (username, banned_until, ban_reason),
            )
            manifest.data["bans_added"].append(username)
        migrated += 1

    prosody_db.commit()
    log(f"  Bans: {migrated} migrated, {expired} expired (skipped)")
    return migrated


def migrate_file_bans(old_db, prosody_db, manifest, dry_run=False):
    """Migrate file type bans to thrive_file_bans."""
    rows = old_db.execute(
        "SELECT username, file_type, until_date, reason FROM file_bans"
    ).fetchall()

    migrated = 0

    for row in rows:
        username = row["username"].strip().lower()
        file_type = (row["file_type"] or "").strip().lower()
        until_date = row["until_date"]
        reason = row["reason"] or ""

        if not username or not file_type:
            continue

        if dry_run:
            log(f"  [DRY RUN] Would migrate file ban: {username} / {file_type}")
        else:
            prosody_db.execute(
                "INSERT OR REPLACE INTO thrive_file_bans "
                "(username, file_type, until_date, reason) VALUES (?, ?, ?, ?)",
                (username, file_type, until_date, reason),
            )
            manifest.data["file_bans_added"].append([username, file_type])
        migrated += 1

    prosody_db.commit()
    log(f"  File bans: {migrated} migrated")
    return migrated


def migrate_admins(admins_file, prosody_db, manifest, dry_run=False):
    """Migrate admins from admins.txt to thrive_admins."""
    if not os.path.exists(admins_file):
        log(f"  No admins file found at {admins_file}, skipping")
        return 0

    with open(admins_file, "r") as f:
        admins = {line.strip().lower() for line in f if line.strip()}

    migrated = 0

    for username in sorted(admins):
        if dry_run:
            log(f"  [DRY RUN] Would add admin: {username}")
        else:
            prosody_db.execute(
                "INSERT OR REPLACE INTO thrive_admins (username) VALUES (?)",
                (username,),
            )
            manifest.data["admins_added"].append(username)
        migrated += 1

    prosody_db.commit()
    log(f"  Admins: {migrated} migrated")
    return migrated


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback(manifest_path):
    """Undo a migration using a previously saved manifest."""
    if not os.path.exists(manifest_path):
        print(f"Error: Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path) as f:
        data = json.load(f)

    domain = data["domain"]
    prosody_db_path = data["prosody_db"]

    log(f"Rolling back migration from {data['started_at']}")
    log(f"Domain: {domain}")
    log(f"Prosody DB: {prosody_db_path}")
    print()

    # Verify prosodyctl is available.
    ok, out = prosodyctl("about")
    if not ok:
        print(f"Error: prosodyctl is not available: {out}", file=sys.stderr)
        sys.exit(1)

    errors = 0

    # 1. Delete Prosody accounts that were created.
    users = data.get("users_created", [])
    if users:
        log(f"Removing {len(users)} Prosody accounts...")
        for username in users:
            ok, out = prosodyctl("deluser", f"{username}@{domain}")
            if ok:
                log(f"  Deleted user: {username}")
            else:
                warn(f"  Failed to delete user {username}: {out}")
                errors += 1

    # 2. Remove roster files that were written.
    roster_files = data.get("roster_files_written", [])
    if roster_files:
        log(f"Removing {len(roster_files)} roster files...")
        for path in roster_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    log(f"  Deleted roster file: {path}")
                else:
                    log(f"  Roster file already gone: {path}")
            except OSError as e:
                warn(f"  Failed to delete roster file {path}: {e}")
                errors += 1

    # 3. Clean up thrive.db tables.
    if os.path.exists(prosody_db_path):
        conn = sqlite3.connect(prosody_db_path)
        conn.execute("PRAGMA journal_mode=WAL")

        for username in data.get("legacy_hashes_stored", []):
            conn.execute("DELETE FROM thrive_legacy_passwords WHERE username = ?", (username,))
        for username in data.get("emails_stored", []):
            conn.execute("DELETE FROM thrive_emails WHERE username = ?", (username,))
        for username in data.get("bans_added", []):
            conn.execute("DELETE FROM thrive_bans WHERE username = ?", (username,))
        for username, file_type in data.get("file_bans_added", []):
            conn.execute(
                "DELETE FROM thrive_file_bans WHERE username = ? AND file_type = ?",
                (username, file_type),
            )
        for username in data.get("admins_added", []):
            conn.execute("DELETE FROM thrive_admins WHERE username = ?", (username,))

        conn.commit()
        conn.close()
        log("  Cleaned thrive.db tables.")

    print()
    if errors:
        warn(f"Rollback completed with {errors} error(s). Check output above.")
    else:
        log("Rollback complete. All migration actions have been reversed.")

    backup_path = data.get("backup_path")
    if backup_path and os.path.isdir(backup_path):
        print()
        log(f"A pre-migration backup also exists at: {backup_path}")
        log("  You can manually restore from it if needed:")
        log(f"    cp {backup_path}/thrive.db {prosody_db_path}")
        log(f"    cp -r {backup_path}/prosody-data/* /var/lib/prosody/")


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(users_created, manifest_path, domain):
    """Print a summary of the migration with next steps."""
    print()
    print("=" * 60)
    print("  Migration Complete")
    print("=" * 60)
    print()

    if users_created:
        print(f"  {users_created} new Prosody accounts created.")
        print()
        print("  Users keep their existing passwords — no reset required.")
        print("  On first login, mod_auth_thrive verifies the old argon2")
        print("  hash and transparently re-hashes to SCRAM-SHA-1.")
    else:
        print("  No new accounts were created.")

    print()
    print(f"  Manifest: {manifest_path}")
    print("  To undo this migration:")
    print(f"    python3 srv/migrate_accounts.py --rollback {manifest_path}")

    print()
    print("  Next steps:")
    print("  1. Fix file ownership:")
    print("       sudo chown -R prosody:prosody /var/lib/prosody")
    print("  2. Deploy custom Thrive modules to Prosody:")
    print("       sudo cp prosody/modules/*.lua /etc/prosody/thrive-modules/")
    print("       sudo cp prosody/modules/verify_argon2.py /etc/prosody/thrive-modules/")
    print("  3. Switch authentication in prosody.cfg.lua:")
    print("       authentication = \"thrive\"")
    print("  4. Restart Prosody:")
    print("       sudo systemctl restart prosody")
    print("  5. Test login with a migrated account (same password as before)")
    print("  6. Verify contacts appear in the roster")
    print("  7. Verify bans are enforced")
    print("  8. Register bot accounts if not already done:")
    print(f"       prosodyctl register assistant-bot {domain} <password>")
    print(f"       prosodyctl register helper-bot {domain} <password>")
    print("  9. Once all users have logged in at least once, legacy hashes")
    print("     are gone.  You can switch authentication back to")
    print("     \"internal_hashed\" for SCRAM-only auth (optional).")
    print(" 10. Once verified, the old srv/server.py can be decommissioned")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Migrate Thrive Messenger accounts from legacy server to Prosody.",
    )
    parser.add_argument(
        "--old-db", default="srv/thrive.db",
        help="Path to the legacy thrive.db (default: srv/thrive.db)",
    )
    parser.add_argument(
        "--prosody-db", default="/var/lib/prosody/thrive.db",
        help="Path to the Prosody thrive.db (default: /var/lib/prosody/thrive.db)",
    )
    parser.add_argument(
        "--prosody-data", default="/var/lib/prosody",
        help="Path to Prosody's internal data directory (default: /var/lib/prosody)",
    )
    parser.add_argument(
        "--domain", default="msg.thecubed.cc",
        help="XMPP domain for the Prosody server (default: msg.thecubed.cc)",
    )
    parser.add_argument(
        "--admins-file", default="srv/admins.txt",
        help="Path to the legacy admins.txt (default: srv/admins.txt)",
    )
    parser.add_argument(
        "--backup-dir", default=".",
        help="Directory to store backups and manifests (default: current directory)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without making changes.",
    )
    parser.add_argument(
        "--rollback", metavar="MANIFEST",
        help="Undo a previous migration using the specified manifest JSON file.",
    )
    parser.add_argument(
        "--contacts-only", action="store_true",
        help="Only migrate contacts (skip accounts, bans, file bans, admins).",
    )
    args = parser.parse_args()

    # --- Rollback mode ---
    if args.rollback:
        rollback(args.rollback)
        return

    # --- Migration mode ---
    log(f"Old database:   {args.old_db}")
    log(f"Prosody DB:     {args.prosody_db}")
    log(f"Prosody data:   {args.prosody_data}")
    log(f"Domain:         {args.domain}")
    log(f"Admins file:    {args.admins_file}")
    log(f"Backup dir:     {args.backup_dir}")
    if args.dry_run:
        log("*** DRY RUN MODE — no changes will be made ***")
    print()

    # Verify prosodyctl is available (unless dry run).
    if not args.dry_run:
        ok, out = prosodyctl("about")
        if not ok:
            print(f"Error: prosodyctl is not available: {out}", file=sys.stderr)
            print("Make sure Prosody is installed and prosodyctl is on PATH.",
                  file=sys.stderr)
            sys.exit(1)
        log(f"Prosody: {out.splitlines()[0] if out else 'OK'}")
        print()

    # Step 0: Back up Prosody state before touching anything.
    if not args.dry_run:
        log("Step 0: Creating pre-migration backup...")
        backup_path = create_backup(
            args.prosody_db, args.prosody_data, args.backup_dir,
        )
        if not backup_path:
            print("Error: Backup failed. Aborting migration.", file=sys.stderr)
            print("Fix the backup issue or use --dry-run to preview.", file=sys.stderr)
            sys.exit(1)
        print()

    old_db = open_old_db(args.old_db)
    prosody_db = open_prosody_db(args.prosody_db)
    manifest = Manifest(args.domain, args.prosody_db)

    if not args.dry_run:
        manifest.data["backup_path"] = backup_path

    users_created = 0

    if args.contacts_only:
        # Only migrate contacts — skip everything else.
        log("Migrating contacts only...")
        migrate_contacts(
            old_db, args.domain, args.prosody_data, manifest, dry_run=args.dry_run,
        )
    else:
        # Step 1: Migrate users (stores argon2 hashes for lazy rehash on login).
        log("Step 1: Migrating user accounts...")
        users_created = migrate_users(
            old_db, prosody_db, args.domain, manifest, dry_run=args.dry_run,
        )

        # Step 2: Migrate contacts (writes roster files directly to Prosody storage).
        log("Step 2: Migrating contacts to XMPP roster...")
        migrate_contacts(
            old_db, args.domain, args.prosody_data, manifest, dry_run=args.dry_run,
        )

        # Step 3: Migrate bans.
        log("Step 3: Migrating user bans...")
        migrate_bans(old_db, prosody_db, manifest, dry_run=args.dry_run)

        # Step 4: Migrate file bans.
        log("Step 4: Migrating file type bans...")
        migrate_file_bans(old_db, prosody_db, manifest, dry_run=args.dry_run)

        # Step 5: Migrate admins.
        log("Step 5: Migrating admin list...")
        migrate_admins(args.admins_file, prosody_db, manifest, dry_run=args.dry_run)

    old_db.close()
    prosody_db.close()

    # Save manifest.
    if not args.dry_run:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        manifest_path = os.path.join(
            args.backup_dir, f"migration-manifest-{timestamp}.json",
        )
        manifest.save(manifest_path)
        print()
        print_summary(users_created, manifest_path, args.domain)
    else:
        log("Dry run complete. No changes were made.")


if __name__ == "__main__":
    main()
