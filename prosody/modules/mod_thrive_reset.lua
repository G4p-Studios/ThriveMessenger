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
--   thrive_mail_transport = "sendmail"  -- "sendmail" (default) or "socket"
--   thrive_sendmail      = "/usr/bin/msmtp"       -- sendmail transport
--   thrive_smtp_server   = "smtp.example.com"     -- socket transport only
--   thrive_smtp_port     = 587                    -- socket transport only
--   thrive_smtp_user     = "noreply@example.com"  -- socket transport only
--   thrive_smtp_password = "secret"               -- socket transport only
--   thrive_smtp_from     = "noreply@example.com"  -- envelope + From: header
--   thrive_code_expires  = 300   -- seconds (default 5 minutes)
--   thrive_reset_cooldown = 60   -- min seconds between reset emails per user
--   thrive_db_path       = "/var/lib/prosody/thrive.db"
--
-- Requires "additional_registration_fields = { \"email\" }" so that
-- mod_register_ibr keeps the address clients send at registration; without
-- it no account has an email on record and nothing can be sent.
--
-- On transports: luasocket's socket.smtp speaks plaintext SMTP only -- it
-- has no STARTTLS support -- so it cannot reach submission ports that
-- require encryption (587 on essentially every provider).  The default
-- "sendmail" transport pipes the message to msmtp or similar, which does
-- STARTTLS properly and keeps credentials out of this config file.

local st = require "util.stanza";
local usermanager = require "core.usermanager";
local timer = require "util.timer";

local log = module._log;

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

local mail_transport = module:get_option_string("thrive_mail_transport", "sendmail");
local sendmail_path = module:get_option_string("thrive_sendmail", "/usr/bin/msmtp");
local smtp_server   = module:get_option_string("thrive_smtp_server", "");
local smtp_port     = module:get_option_number("thrive_smtp_port", 587);
local smtp_user     = module:get_option_string("thrive_smtp_user", "");
local smtp_password = module:get_option_string("thrive_smtp_password", "");
local smtp_from     = module:get_option_string("thrive_smtp_from", smtp_user);
local code_expires  = module:get_option_number("thrive_code_expires", 300);
local reset_cooldown = module:get_option_number("thrive_reset_cooldown", 60);
local db_path       = module:get_option_string("thrive_db_path", "thrive.db");

local smtp_enabled;
if mail_transport == "sendmail" then
    -- Credentials live in the mailer's own config, so all we need is a
    -- usable From: address.
    smtp_enabled = smtp_from ~= "";
else
    smtp_enabled = smtp_server ~= "" and smtp_user ~= "";
end

local host = module.host;

-- Extra registration fields are stored here by mod_register_ibr; it does
-- not pass them on the user-registered event.
local account_details = module:open_store("account_details");

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

--- Quote a string for safe use as a single shell argument.
local function shellquote(s)
    local quoted = tostring(s):gsub("'", "'\\''");
    return "'" .. quoted .. "'";
end

--- Send via a sendmail-compatible binary (msmtp, sendmail, ssmtp).
-- This is the transport that works with submission ports requiring
-- STARTTLS, which socket.smtp below cannot do.  The message is piped to
-- the mailer's stdin, so nothing sensitive touches a temp file.
local function send_via_sendmail(to, subject, body)
    -- "timeout" bounds the call: io.popen blocks Prosody's event loop.
    local cmd = string.format(
        "timeout 20 %s -f %s -- %s 2>&1",
        sendmail_path, shellquote(smtp_from), shellquote(to)
    );

    local pipe, popen_err = io.popen(cmd, "w");
    if not pipe then
        log("warn", "Could not run %s: %s", sendmail_path, tostring(popen_err));
        return false;
    end

    pipe:write("From: ", smtp_from, "\r\n");
    pipe:write("To: ", to, "\r\n");
    pipe:write("Subject: ", subject, "\r\n");
    pipe:write("Content-Type: text/plain; charset=utf-8\r\n");
    pipe:write("\r\n");
    pipe:write(body, "\r\n");

    local ok, exit_kind, code = pipe:close();
    if not ok then
        log("warn", "Mailer %s failed for %s (%s %s)",
            sendmail_path, to, tostring(exit_kind), tostring(code));
        return false;
    end
    return true;
end

--- Send via luasocket's SMTP client.
-- Plaintext only -- luasocket has no STARTTLS support -- so this suits a
-- local relay on port 25 and little else.  Kept for that case.
local function send_via_socket(to, subject, body)
    local smtp_lib = require "socket.smtp";

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

--- Send an email.  Returns true only if it was actually handed off.
local function send_email(to, subject, body)
    if not smtp_enabled then
        log("warn", "Mail not configured; dropping %q to %s", subject, to);
        return false;
    end
    if mail_transport == "sendmail" then
        return send_via_sendmail(to, subject, body);
    end
    return send_via_socket(to, subject, body);
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
-- Pre-authentication support
--
-- Verify and reset both happen before the user can log in -- a password reset
-- by definition has no usable password.  Prosody only routes stanzas to
-- "iq/host" once a session is authenticated and resource-bound, so these two
-- namespaces are also served on the unauthenticated "stanza/iq/..." events,
-- the same mechanism mod_register_ibr uses for in-band registration.
--
-- Clients cannot guess whether a server supports this, so we advertise a
-- stream feature to unauthenticated sessions.
-- ---------------------------------------------------------------------------

local PREAUTH_NS = "urn:thrive:preauth";
local preauth_feature = st.stanza("preauth", { xmlns = PREAUTH_NS });

module:hook("stream-features", function(event)
    local session, features = event.origin, event.features;
    -- Only to clients that have not logged in, and only over TLS: these
    -- exchanges carry emailed codes and new passwords.
    if session.type ~= "c2s_unauthed" or not session.secure then return; end
    features:add_child(preauth_feature);
end);

--- Wrap a handler so it only serves unauthenticated sessions.
-- The same handler is bound to "iq/host" for logged-in users; without this
-- guard an authenticated stanza could be processed twice.
local function preauth_only(handler)
    return function(event)
        local session = event.origin;
        if session.type ~= "c2s_unauthed" then return; end
        if not session.secure then
            session.send(st.error_reply(event.stanza, "modify", "policy-violation",
                "Encryption is required."));
            return true;
        end
        return handler(event);
    end
end

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:verify
-- ---------------------------------------------------------------------------

local function handle_verify(event)
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
end

module:hook("iq/host", handle_verify);
module:hook("stanza/iq/urn:thrive:verify:verify", preauth_only(handle_verify));

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:reset  (request + confirm)
-- ---------------------------------------------------------------------------

local function handle_reset(event)
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

        -- "unavailable" unless a code is actually waiting in the user's
        -- inbox.  Reporting success when no mail went out sends people off
        -- to hunt for a code that was never generated.
        local status = "unavailable";

        if target_user and target_email then
            -- This endpoint is reachable without logging in, so throttle it:
            -- an outstanding code stays valid instead of triggering more mail.
            local recent = conn:prepare("SELECT created_at FROM thrive_reset WHERE username = ?");
            recent:execute(target_user);
            local rrow = recent:fetch(true);
            local throttled = rrow and (os.time() - rrow.created_at) < reset_cooldown;

            if throttled then
                log("debug", "Reset code for %s requested again within %d seconds; not resending",
                    target_user, reset_cooldown);
                status = "throttled";
            else
                local code = generate_code();
                local now = os.time();
                local ups = conn:prepare(
                    "INSERT OR REPLACE INTO thrive_reset (username, code, created_at) VALUES (?, ?, ?)"
                );
                ups:execute(target_user, code, now);

                if send_email(
                    target_email,
                    "Thrive Messenger - Password Reset",
                    "Your password reset code is: " .. code ..
                    "\n\nThis code will expire in " .. expire_human() .. "."
                ) then
                    status = "sent";
                else
                    -- Drop the code we just stored; it is unreachable, and
                    -- leaving it would throttle the next honest attempt.
                    local del = conn:prepare("DELETE FROM thrive_reset WHERE username = ?");
                    del:execute(target_user);
                    log("error", "Reset code for %s could not be mailed to %s",
                        target_user, target_email);
                end
            end
        elseif target_user then
            log("info", "Reset requested for %s but no email is on record", target_user);
        end

        local reply = st.reply(stanza);
        reply:tag("status"):text(status):up();
        -- The username hint is only useful when the flow can continue, and
        -- withholding it otherwise narrows what this endpoint discloses.
        if status ~= "unavailable" then
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
end

module:hook("iq/host", handle_reset);
module:hook("stanza/iq/urn:thrive:reset:request", preauth_only(handle_reset));
module:hook("stanza/iq/urn:thrive:reset:confirm", preauth_only(handle_reset));

-- ---------------------------------------------------------------------------
-- Registration hook: require email verification when SMTP is enabled
-- ---------------------------------------------------------------------------

-- Hook into user-registered event to store verification code and send email.
module:hook("user-registered", function(event)
    if not smtp_enabled then return; end

    local username = event.username;
    local session  = event.session;

    -- mod_register_ibr fires this event with only username/host/source/
    -- session -- never the email.  The address a client sent at
    -- registration lands in the account_details store instead, and only if
    -- "email" is listed in additional_registration_fields.
    local email = event.email;
    if not email or email == "" then
        local details = account_details:get(username);
        email = details and details.email or "";
    end

    if email == "" then
        -- No email provided — skip verification, account is immediately usable.
        log("debug", "No email on record for %s; skipping verification", username);
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
