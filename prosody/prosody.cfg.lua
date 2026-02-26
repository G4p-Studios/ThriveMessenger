-- Prosody configuration for Thrive Messenger
-- See https://prosody.im/doc/configure for documentation

---------- Server-wide settings ----------

admins = { }  -- Add admin JIDs here, e.g. "admin@msg.thecubed.cc"

-- Path to custom Thrive modules
plugin_paths = { "/etc/prosody/thrive-modules" }

-- Network interfaces to listen on
interfaces = { "*" }

-- Modules enabled globally
modules_enabled = {
    -- Core
    "roster";
    "saslauth";
    "tls";
    "dialback";
    "disco";
    "posix";
    "ping";
    "pep";
    "register";
    "carbons";

    -- Standard XEPs
    "vcard_legacy";          -- XEP-0054: vCards
    "blocklist";             -- XEP-0191: Blocking
    "csi_simple";            -- XEP-0352: Client State Indication
    "smacks";                -- XEP-0198: Stream Management
    "mam";                   -- XEP-0313: Message Archive Management

    -- File transfers
    "http_file_share";       -- XEP-0363: HTTP File Upload

    -- Admin
    "admin_adhoc";           -- XEP-0133: Service Administration

    -- Registration
    "register_ibr";          -- XEP-0077: In-Band Registration

    -- Thrive custom modules
    "thrive_reset";          -- Email verification + password reset
    "thrive_directory";      -- User directory (urn:thrive:directory)
    "thrive_admin";          -- Admin commands (urn:thrive:admin)
}

modules_disabled = { }

-- TLS configuration
-- Update these paths to your certificate files
ssl = {
    certificate = "/etc/prosody/certs/msg.thecubed.cc.crt";
    key = "/etc/prosody/certs/msg.thecubed.cc.key";
}

-- Require encryption for client connections
c2s_require_encryption = true
s2s_require_encryption = true

-- Authentication
-- Uses custom "thrive" provider for transparent argon2 → SCRAM migration.
-- Once all legacy hashes are gone, you can switch back to "internal_hashed".
authentication = "thrive"

-- Storage (default internal, can switch to sql for larger deployments)
storage = "internal"

-- Logging
log = {
    info = "/var/log/prosody/prosody.log";
    error = "/var/log/prosody/prosody.err";
}

---------- Message Archive Management ----------
archive_expires_after = "1y"  -- Keep messages for 1 year
default_archive_policy = true  -- Archive by default

---------- HTTP File Upload ----------
http_file_share_size_limit = 2684354560  -- 2.5 GB (matches current size_limit)
http_file_share_expires_after = 604800   -- 7 days
http_file_share_daily_quota = 53687091200  -- 50 GB daily per user
http_external_url = "https://msg.thecubed.cc/"

---------- Registration ----------
allow_registration = true
-- registration_throttle_period = 60       -- Seconds between registrations
-- registration_throttle_max = 3           -- Max registrations per period

---------- Thrive: Email Verification & Password Reset ----------
thrive_smtp_server   = "smtp-auth.mythic-beasts.com"
thrive_smtp_port     = 587
thrive_smtp_user     = "tmsg@seedy.cc"
thrive_smtp_password = ""  -- Set via environment or secrets management
thrive_smtp_from     = "tmsg@seedy.cc"
thrive_code_expires  = 3600  -- Seconds before codes expire (1 hour, matches old server)
thrive_db_path       = "/var/lib/prosody/thrive.db"

---------- Thrive: Auth (legacy argon2 migration) ----------
thrive_argon2_verify = "/etc/prosody/thrive-modules/verify_argon2.py"

---------- Thrive: Admin Commands ----------
thrive_shutdown_timeout = 5  -- Seconds delay before shutdown/restart after alert

---------- Virtual host ----------
VirtualHost "msg.thecubed.cc"

-- Components (uncomment if needed)
-- Component "upload.msg.thecubed.cc" "http_file_share"
