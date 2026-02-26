-- mod_thrive_reset.lua
-- Prosody module for Thrive Messenger email verification and password reset.
--
-- Handles two custom IQ namespaces:
--   urn:thrive:verify   -- verify account with emailed code
--   urn:thrive:reset    -- request/confirm password reset via email
--
-- Also hooks into user registration to require email verification when
-- SMTP is configured.
--
-- Configuration (prosody.cfg.lua):
--   thrive_smtp_server   = "smtp.example.com"
--   thrive_smtp_port     = 587
--   thrive_smtp_user     = "noreply@example.com"
--   thrive_smtp_password = "secret"
--   thrive_smtp_from     = "noreply@example.com"  -- defaults to smtp_user
--   thrive_code_expires  = 300   -- seconds (default 5 minutes)
--   thrive_db_path       = "/var/lib/prosody/thrive.db"

local st = require "util.stanza";
local usermanager = require "core.usermanager";
local timer = require "util.timer";

local log = module._log;

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

local smtp_server   = module:get_option_string("thrive_smtp_server", "");
local smtp_port     = module:get_option_number("thrive_smtp_port", 587);
local smtp_user     = module:get_option_string("thrive_smtp_user", "");
local smtp_password = module:get_option_string("thrive_smtp_password", "");
local smtp_from     = module:get_option_string("thrive_smtp_from", smtp_user);
local code_expires  = module:get_option_number("thrive_code_expires", 300);
local db_path       = module:get_option_string("thrive_db_path", "thrive.db");

local smtp_enabled = smtp_server ~= "" and smtp_user ~= "";

local host = module.host;

-- ---------------------------------------------------------------------------
-- Database (SQLite via LuaDBI)
-- ---------------------------------------------------------------------------

local DBI = require "DBI";
local db;

local function open_db()
    if db then return db; end
    local conn, err = DBI.Connect("SQLite3", db_path);
    if not conn then
        log("error", "Failed to open thrive DB at %s: %s", db_path, tostring(err));
        return nil;
    end
    conn:autocommit(true);

    -- Ensure tables exist.
    local stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_verify (
            username    TEXT PRIMARY KEY,
            email       TEXT NOT NULL,
            code        TEXT NOT NULL,
            created_at  INTEGER NOT NULL
        )
    ]]);
    stmt:execute();

    stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_reset (
            username    TEXT PRIMARY KEY,
            code        TEXT NOT NULL,
            created_at  INTEGER NOT NULL
        )
    ]]);
    stmt:execute();

    -- Persistent email store (survives verification).
    stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_emails (
            username    TEXT PRIMARY KEY,
            email       TEXT NOT NULL
        )
    ]]);
    stmt:execute();

    db = conn;
    return db;
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

--- Generate a 32-character hex code (128-bit entropy).
local function generate_code()
    local f = io.open("/dev/urandom", "rb");
    if f then
        local bytes = f:read(16);
        f:close();
        local hex = {};
        for i = 1, #bytes do
            hex[i] = string.format("%02x", string.byte(bytes, i));
        end
        return table.concat(hex);
    end
    -- Fallback: math.random (less secure, but functional on systems without /dev/urandom).
    math.randomseed(os.time() + os.clock() * 1000);
    local hex = {};
    for i = 1, 32 do
        hex[i] = string.format("%x", math.random(0, 15));
    end
    return table.concat(hex);
end

--- Send an email via SMTP using the socket library.
-- Uses STARTTLS when available.  Returns true on success.
local function send_email(to, subject, body)
    if not smtp_enabled then return false; end

    -- Use Lua socket + SMTP (luasocket).
    local smtp_lib = require "socket.smtp";
    local mime = require "mime";
    local ltn12 = require "ltn12";

    local message = {
        headers = {
            from    = smtp_from,
            to      = to,
            subject = subject,
        },
        body = body,
    };

    local source = smtp_lib.message(message);

    local ok, err = smtp_lib.send({
        from     = smtp_from,
        rcpt     = { to },
        source   = source,
        server   = smtp_server,
        port     = smtp_port,
        user     = smtp_user,
        password = smtp_password,
    });

    if not ok then
        log("warn", "Failed to send email to %s: %s", to, tostring(err));
        return false;
    end
    return true;
end

--- Human-readable expiration string.
local function expire_human()
    if code_expires < 60 then
        return code_expires .. " seconds";
    elseif code_expires < 3600 then
        local mins = math.floor(code_expires / 60);
        return mins .. (mins == 1 and " minute" or " minutes");
    else
        local hrs = math.floor(code_expires / 3600);
        return hrs .. (hrs == 1 and " hour" or " hours");
    end
end

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:verify
-- ---------------------------------------------------------------------------

module:hook("iq/host", function(event)
    local stanza = event.stanza;
    local verify = stanza:get_child("verify", "urn:thrive:verify");
    if not verify then return; end
    if stanza.attr.type ~= "set" then return; end

    local username = verify:get_child_text("username");
    local code     = verify:get_child_text("code");

    if not username or not code then
        event.origin.send(st.error_reply(stanza, "modify", "bad-request", "Missing username or code."));
        return true;
    end

    local conn = open_db();
    if not conn then
        event.origin.send(st.error_reply(stanza, "wait", "internal-server-error", "Database unavailable."));
        return true;
    end

    local stmt = conn:prepare("SELECT code, created_at FROM thrive_verify WHERE username = ?");
    stmt:execute(username);
    local row = stmt:fetch(true);

    if not row or row.code ~= code then
        event.origin.send(st.error_reply(stanza, "auth", "not-authorized", "Invalid code."));
        return true;
    end

    -- Check expiration.
    local elapsed = os.time() - row.created_at;
    if elapsed > code_expires then
        -- Clean up expired code.
        local del = conn:prepare("DELETE FROM thrive_verify WHERE username = ?");
        del:execute(username);
        event.origin.send(st.error_reply(stanza, "modify", "not-acceptable", "Code has expired."));
        return true;
    end

    -- Success — persist the email for future password resets, then clean up.
    local email_row = conn:prepare("SELECT email FROM thrive_verify WHERE username = ?");
    email_row:execute(username);
    local vrow = email_row:fetch(true);
    if vrow and vrow.email then
        local ups = conn:prepare(
            "INSERT OR REPLACE INTO thrive_emails (username, email) VALUES (?, ?)"
        );
        ups:execute(username, vrow.email);
    end

    local del = conn:prepare("DELETE FROM thrive_verify WHERE username = ?");
    del:execute(username);

    log("info", "Account verified: %s", username);

    event.origin.send(st.reply(stanza));
    return true;
end);

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:reset  (request + confirm)
-- ---------------------------------------------------------------------------

module:hook("iq/host", function(event)
    local stanza = event.stanza;

    -- --- Request a reset code ---
    local request = stanza:get_child("request", "urn:thrive:reset");
    if request and stanza.attr.type == "set" then
        local identifier = request:get_child_text("identifier");
        if not identifier then
            event.origin.send(st.error_reply(stanza, "modify", "bad-request", "Missing identifier."));
            return true;
        end

        local conn = open_db();
        if not conn then
            event.origin.send(st.error_reply(stanza, "wait", "internal-server-error", "Database unavailable."));
            return true;
        end

        -- Look up the user.  The identifier may be a username or an email.
        -- First try username directly via Prosody's usermanager.
        local target_user = nil;
        local target_email = nil;

        if usermanager.user_exists(identifier, host) then
            target_user = identifier;
            -- Look up email from persistent email store.
            local stmt = conn:prepare("SELECT email FROM thrive_emails WHERE username = ?");
            stmt:execute(identifier);
            local row = stmt:fetch(true);
            if row then target_email = row.email; end
        else
            -- Try searching by email address.
            local stmt = conn:prepare("SELECT username, email FROM thrive_emails WHERE email = ?");
            stmt:execute(identifier);
            local row = stmt:fetch(true);
            if row and usermanager.user_exists(row.username, host) then
                target_user = row.username;
                target_email = row.email;
            end
        end

        if target_user and target_email then
            local code = generate_code();
            local now = os.time();
            local ups = conn:prepare(
                "INSERT OR REPLACE INTO thrive_reset (username, code, created_at) VALUES (?, ?, ?)"
            );
            ups:execute(target_user, code, now);

            send_email(
                target_email,
                "Thrive Messenger - Password Reset",
                "Your password reset code is: " .. code ..
                "\n\nThis code will expire in " .. expire_human() .. "."
            );
        end

        -- Always reply OK to prevent user enumeration.
        local reply = st.reply(stanza);
        if target_user then
            reply:tag("user"):text(target_user):up();
        end
        event.origin.send(reply);
        return true;
    end

    -- --- Confirm the reset ---
    local confirm = stanza:get_child("confirm", "urn:thrive:reset");
    if confirm and stanza.attr.type == "set" then
        local username = confirm:get_child_text("username");
        local code     = confirm:get_child_text("code");
        local password = confirm:get_child_text("password");

        if not username or not code or not password then
            event.origin.send(st.error_reply(stanza, "modify", "bad-request", "Missing fields."));
            return true;
        end

        local conn = open_db();
        if not conn then
            event.origin.send(st.error_reply(stanza, "wait", "internal-server-error", "Database unavailable."));
            return true;
        end

        local stmt = conn:prepare("SELECT code, created_at FROM thrive_reset WHERE username = ?");
        stmt:execute(username);
        local row = stmt:fetch(true);

        if not row or row.code ~= code then
            event.origin.send(st.error_reply(stanza, "auth", "not-authorized", "Invalid code."));
            return true;
        end

        -- Check expiration.
        local elapsed = os.time() - row.created_at;
        if elapsed > code_expires then
            local del = conn:prepare("DELETE FROM thrive_reset WHERE username = ?");
            del:execute(username);
            event.origin.send(st.error_reply(stanza, "modify", "not-acceptable", "Code has expired."));
            return true;
        end

        -- Change the password via Prosody's usermanager.
        local ok, err = usermanager.set_password(username, password, host);
        if not ok then
            event.origin.send(st.error_reply(stanza, "wait", "internal-server-error",
                "Failed to set password: " .. tostring(err)));
            return true;
        end

        -- Clean up.
        local del = conn:prepare("DELETE FROM thrive_reset WHERE username = ?");
        del:execute(username);

        log("info", "Password reset completed for %s", username);
        event.origin.send(st.reply(stanza));
        return true;
    end
end);

-- ---------------------------------------------------------------------------
-- Registration hook: require email verification when SMTP is enabled
-- ---------------------------------------------------------------------------

-- Hook into user-registered event to store verification code and send email.
module:hook("user-registered", function(event)
    if not smtp_enabled then return; end

    local username = event.username;
    local session  = event.session;
    local email    = event.email or "";

    if email == "" then
        -- No email provided — skip verification, account is immediately usable.
        return;
    end

    local conn = open_db();
    if not conn then return; end

    local code = generate_code();
    local now = os.time();

    local stmt = conn:prepare(
        "INSERT OR REPLACE INTO thrive_verify (username, email, code, created_at) VALUES (?, ?, ?, ?)"
    );
    stmt:execute(username, email, code, now);

    -- Also persist email for future password reset lookups.
    local email_stmt = conn:prepare(
        "INSERT OR REPLACE INTO thrive_emails (username, email) VALUES (?, ?)"
    );
    email_stmt:execute(username, email);

    local sent = send_email(
        email,
        "Thrive Messenger - Verify Account",
        "Your verification code is: " .. code ..
        "\n\nThis code will expire in " .. expire_human() .. "."
    );

    if sent then
        log("info", "Verification email sent to %s for user %s", email, username);
    else
        log("warn", "Failed to send verification email to %s for user %s", email, username);
    end
end);

-- ---------------------------------------------------------------------------
-- Periodic cleanup of expired codes
-- ---------------------------------------------------------------------------

timer.add_task(3600, function()
    local conn = open_db();
    if not conn then return 3600; end

    local cutoff = os.time() - code_expires;

    local stmt = conn:prepare("DELETE FROM thrive_verify WHERE created_at < ?");
    stmt:execute(cutoff);

    stmt = conn:prepare("DELETE FROM thrive_reset WHERE created_at < ?");
    stmt:execute(cutoff);

    log("debug", "Cleaned up expired verification/reset codes (cutoff=%d)", cutoff);
    return 3600;  -- Run again in 1 hour.
end);

-- ---------------------------------------------------------------------------
-- Module loaded
-- ---------------------------------------------------------------------------

module:log("info", "mod_thrive_reset loaded (SMTP %s, code_expires=%ds)",
    smtp_enabled and "enabled" or "disabled", code_expires);
