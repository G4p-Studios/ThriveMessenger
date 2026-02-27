-- mod_thrive_admin.lua
-- Prosody module for Thrive Messenger server-side admin commands.
--
-- Handles custom IQ namespace: urn:thrive:admin
--
-- Client sends:
--   <iq type="set" to="msg.thecubed.cc">
--     <command xmlns="urn:thrive:admin">ban alice 12/31/2026 spamming</command>
--   </iq>
--
-- Server responds:
--   <iq type="result">
--     <response xmlns="urn:thrive:admin">User 'alice' banned.</response>
--   </iq>
--
-- Configuration (prosody.cfg.lua):
--   thrive_shutdown_timeout = 5   -- seconds before shutdown/restart
--   thrive_db_path = "/var/lib/prosody/thrive.db"

local st = require "util.stanza";
local usermanager = require "core.usermanager";
local jid_join = require "util.jid".join;
local timer = require "util.timer";

local log = module._log;
local host = module.host;

local NS = "urn:thrive:admin";

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

local db_path = module:get_option_string("thrive_db_path", "thrive.db");
local shutdown_timeout = module:get_option_number("thrive_shutdown_timeout", 5);

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

    -- Ban storage
    local stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_bans (
            username    TEXT PRIMARY KEY,
            banned_until TEXT NOT NULL,
            ban_reason  TEXT
        )
    ]]);
    stmt:execute();

    -- File ban storage
    stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_file_bans (
            username    TEXT NOT NULL,
            file_type   TEXT NOT NULL,
            until_date  TEXT,
            reason      TEXT,
            PRIMARY KEY (username, file_type)
        )
    ]]);
    stmt:execute();

    -- Dynamic admin list (supplements prosody.cfg.lua admins)
    stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_admins (
            username TEXT PRIMARY KEY
        )
    ]]);
    stmt:execute();

    db = conn;
    return db;
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

--- Check if a user is a Thrive admin (static config OR dynamic DB grant).
local function is_thrive_admin(username)
    -- Static admins from prosody.cfg.lua
    if usermanager.is_admin(jid_join(username, host), host) then
        return true;
    end
    -- Dynamic admins from database
    local conn = open_db();
    if not conn then return false; end
    local stmt = conn:prepare("SELECT 1 FROM thrive_admins WHERE username = ?");
    stmt:execute(username);
    local row = stmt:fetch();
    return row ~= nil;
end

--- Split a string on whitespace into a table of parts.
local function split(str)
    local parts = {};
    for word in str:gmatch("%S+") do
        parts[#parts + 1] = word;
    end
    return parts;
end

--- Parse a MM/DD/YYYY date string to ISO YYYY-MM-DD.  Returns nil on failure.
local function parse_date_mmddyyyy(date_str)
    local m, d, y = date_str:match("^(%d%d?)/(%d%d?)/(%d%d%d%d)$");
    if not m then return nil; end
    m, d, y = tonumber(m), tonumber(d), tonumber(y);
    if m < 1 or m > 12 or d < 1 or d > 31 then return nil; end
    return string.format("%04d-%02d-%02d", y, m, d);
end

--- Broadcast a headline message to every online session on this host.
local function broadcast_alert(message_text)
    local sessions = prosody.hosts[host] and prosody.hosts[host].sessions;
    if not sessions then return; end
    for username, user_sessions in pairs(sessions) do
        for resource, session in pairs(user_sessions.sessions or {}) do
            local msg = st.message({
                from = host,
                to = jid_join(username, host) .. "/" .. resource,
                type = "headline",
            }):tag("body"):text(message_text):up();
            session.send(msg);
        end
    end
end

--- Close all sessions for a user (kick them off the server).
local function kick_user(username, reason)
    local user_sessions = prosody.hosts[host] and prosody.hosts[host].sessions
        and prosody.hosts[host].sessions[username];
    if not user_sessions then return; end
    for resource, session in pairs(user_sessions.sessions or {}) do
        session:close({
            condition = "not-authorized",
            text = reason or "You have been disconnected by an admin.",
        });
    end
end

-- ---------------------------------------------------------------------------
-- Command handlers
-- Each receives (parts, admin_username) and returns a response string.
-- ---------------------------------------------------------------------------

local function cmd_exit(parts, admin_username)
    log("info", "Shutdown initiated by admin: %s", admin_username);
    broadcast_alert("The server is shutting down in " .. shutdown_timeout .. " seconds.");
    timer.add_task(shutdown_timeout, function()
        log("info", "Shutdown timer fired — calling prosody.shutdown()");
        local ok, err = pcall(prosody.shutdown, "Admin shutdown by " .. admin_username);
        if not ok then
            log("error", "prosody.shutdown() failed: %s", tostring(err));
        end
    end);
    return "Server shutting down in " .. shutdown_timeout .. " seconds...";
end

local function cmd_restart(parts, admin_username)
    log("info", "Restart initiated by admin: %s", admin_username);
    broadcast_alert("The server is restarting in " .. shutdown_timeout .. " seconds.");
    timer.add_task(shutdown_timeout, function()
        log("info", "Restart timer fired — forking restart helper, then shutting down");
        -- Fork a helper that waits for Prosody to stop, then starts it again.
        -- Try systemctl first (when running as a systemd service), fall back to prosodyctl.
        os.execute("(sleep 2 && (systemctl start prosody 2>/dev/null || /usr/bin/prosodyctl start)) &");
        local ok, err = pcall(prosody.shutdown, "Admin restart by " .. admin_username);
        if not ok then
            log("error", "prosody.shutdown() failed during restart: %s", tostring(err));
        end
    end);
    return "Server restarting in " .. shutdown_timeout .. " seconds...";
end

local function cmd_alert(parts)
    if #parts < 2 then return "Error: Usage: alert <message>"; end
    local message = table.concat(parts, " ", 2);
    broadcast_alert(message);
    return "Alert sent to all online users.";
end

local function cmd_create(parts)
    if #parts < 3 or #parts > 4 then
        return "Error: Usage: create <username> <password> [email]";
    end
    local username = parts[2];
    local password = parts[3];
    local email = parts[4] or "";

    if usermanager.user_exists(username, host) then
        return "Error: Username '" .. username .. "' is already taken.";
    end

    local ok, err = usermanager.create_user(username, password, host);
    if not ok then
        return "Error: Failed to create user: " .. tostring(err);
    end

    -- Store email if provided (reuse thrive_emails table from mod_thrive_reset).
    if email ~= "" then
        local conn = open_db();
        if conn then
            local stmt = conn:prepare(
                "INSERT OR REPLACE INTO thrive_emails (username, email) VALUES (?, ?)"
            );
            stmt:execute(username, email);
        end
    end

    return "User '" .. username .. "' created.";
end

local function cmd_ban(parts)
    if #parts < 4 then
        return "Error: Usage: ban <username> <MM/DD/YYYY> <reason>";
    end
    local username = parts[2];
    local date_str = parts[3];
    local reason = table.concat(parts, " ", 4);

    local iso_date = parse_date_mmddyyyy(date_str);
    if not iso_date then
        return "Error: Date format must be MM/DD/YYYY.";
    end

    if not usermanager.user_exists(username, host) then
        return "Error: User '" .. username .. "' does not exist.";
    end

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    local stmt = conn:prepare(
        "INSERT OR REPLACE INTO thrive_bans (username, banned_until, ban_reason) VALUES (?, ?, ?)"
    );
    stmt:execute(username, iso_date, reason);

    -- Kick if online.
    kick_user(username, "Banned until " .. date_str .. ": " .. reason);

    return "User '" .. username .. "' banned.";
end

local function cmd_unban(parts)
    if #parts ~= 2 then
        return "Error: Usage: unban <username>";
    end
    local username = parts[2];

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    local stmt = conn:prepare("DELETE FROM thrive_bans WHERE username = ?");
    stmt:execute(username);

    return "User '" .. username .. "' unbanned.";
end

local function cmd_del(parts)
    if #parts ~= 2 then
        return "Error: Usage: del <username>";
    end
    local username = parts[2];

    if not usermanager.user_exists(username, host) then
        return "Error: User '" .. username .. "' does not exist.";
    end

    -- Kick first.
    kick_user(username, "Your account has been deleted.");

    -- Delete from Prosody.
    local ok, err = usermanager.delete_user(username, host);
    if not ok then
        return "Error: Failed to delete user: " .. tostring(err);
    end

    -- Clean up thrive tables.
    local conn = open_db();
    if conn then
        local cleanup_tables = {
            "thrive_bans", "thrive_file_bans", "thrive_admins",
            "thrive_emails", "thrive_verify", "thrive_reset",
        };
        for _, tbl in ipairs(cleanup_tables) do
            local stmt = conn:prepare("DELETE FROM " .. tbl .. " WHERE username = ?");
            stmt:execute(username);
        end
    end

    return "User '" .. username .. "' deleted.";
end

local function cmd_admin(parts)
    if #parts ~= 2 then
        return "Error: Usage: admin <username>";
    end
    local username = parts[2];

    if not usermanager.user_exists(username, host) then
        return "Error: User '" .. username .. "' does not exist.";
    end

    if is_thrive_admin(username) then
        return "User '" .. username .. "' is already an admin.";
    end

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    local stmt = conn:prepare(
        "INSERT OR REPLACE INTO thrive_admins (username) VALUES (?)"
    );
    stmt:execute(username);

    return "User '" .. username .. "' is now an admin.";
end

local function cmd_unadmin(parts)
    if #parts ~= 2 then
        return "Error: Usage: unadmin <username>";
    end
    local username = parts[2];

    -- Cannot remove static admins via this command.
    if usermanager.is_admin(jid_join(username, host), host) then
        return "Error: '" .. username .. "' is a static admin (configured in prosody.cfg.lua). Remove from config file.";
    end

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    local stmt = conn:prepare("DELETE FROM thrive_admins WHERE username = ?");
    stmt:execute(username);

    return "User '" .. username .. "' is no longer an admin.";
end

local function cmd_banfile(parts)
    -- banfile <user> <filetype> [date MM/DD/YYYY] <reason>
    if #parts < 4 then
        return "Error: Usage: banfile <user> <filetype> [MM/DD/YYYY] <reason>";
    end
    local username = parts[2];
    local file_type = parts[3]:lower();
    local date_str = nil;
    local reason_start = 4;

    -- Check if parts[4] looks like a date.
    if #parts >= 4 and parse_date_mmddyyyy(parts[4]) then
        date_str = parts[4];
        reason_start = 5;
    end

    local reason = #parts >= reason_start
        and table.concat(parts, " ", reason_start)
        or "No reason given";
    local iso_date = date_str and parse_date_mmddyyyy(date_str) or nil;

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    local stmt = conn:prepare(
        "INSERT OR REPLACE INTO thrive_file_bans (username, file_type, until_date, reason) VALUES (?, ?, ?, ?)"
    );
    stmt:execute(username, file_type, iso_date, reason);

    if date_str then
        return "User '" .. username .. "' banned from sending '" .. file_type .. "' files until " .. date_str .. ".";
    else
        return "User '" .. username .. "' permanently banned from sending '" .. file_type .. "' files.";
    end
end

local function cmd_unbanfile(parts)
    if #parts < 2 then
        return "Error: Usage: unbanfile <user> [filetype]";
    end
    local username = parts[2];
    local file_type = parts[3] and parts[3]:lower() or nil;

    local conn = open_db();
    if not conn then return "Error: Database unavailable."; end

    if file_type then
        local stmt = conn:prepare(
            "DELETE FROM thrive_file_bans WHERE username = ? AND file_type = ?"
        );
        stmt:execute(username, file_type);
        return "User '" .. username .. "' file ban for '" .. file_type .. "' removed.";
    else
        local stmt = conn:prepare("DELETE FROM thrive_file_bans WHERE username = ?");
        stmt:execute(username);
        return "All file bans for user '" .. username .. "' removed.";
    end
end

local function cmd_gpolicy()
    return "Error: gpolicy commands are deferred to v2.1 (pending MUC/group chat implementation).";
end

-- ---------------------------------------------------------------------------
-- Command dispatch table
-- ---------------------------------------------------------------------------

local commands = {
    exit      = cmd_exit,
    restart   = cmd_restart,
    alert     = cmd_alert,
    create    = cmd_create,
    ban       = cmd_ban,
    unban     = cmd_unban,
    del       = cmd_del,
    admin     = cmd_admin,
    unadmin   = cmd_unadmin,
    banfile   = cmd_banfile,
    unbanfile = cmd_unbanfile,
    gpolicy   = cmd_gpolicy,
};

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:admin
-- ---------------------------------------------------------------------------

module:hook("iq/host", function(event)
    local stanza = event.stanza;
    local command_el = stanza:get_child("command", NS);
    if not command_el then return; end
    if stanza.attr.type ~= "set" then return; end

    -- Must be authenticated.
    local session = event.origin;
    if not session.username then
        session.send(st.error_reply(stanza, "auth", "not-authorized",
            "You must be logged in to use admin commands."));
        return true;
    end

    -- Must be admin.
    local username = session.username;
    if not is_thrive_admin(username) then
        session.send(st.error_reply(stanza, "auth", "forbidden",
            "You are not authorized to use admin commands."));
        return true;
    end

    -- Parse command string.
    local cmd_text = command_el:get_text() or "";
    local parts = split(cmd_text);
    if #parts == 0 then
        session.send(st.error_reply(stanza, "modify", "bad-request",
            "Empty command."));
        return true;
    end

    local cmd_name = parts[1]:lower();
    local handler = commands[cmd_name];

    local response_text;
    if handler then
        local ok, result = pcall(handler, parts, username);
        if ok then
            response_text = result;
        else
            log("error", "Admin command '%s' failed: %s", cmd_name, tostring(result));
            response_text = "Error: Internal error executing command.";
        end
    else
        response_text = "Error: Unknown command '" .. cmd_name .. "'. "
            .. "Available: exit, restart, alert, create, ban, unban, del, "
            .. "admin, unadmin, banfile, unbanfile, gpolicy";
    end

    -- Build and send response.
    local reply = st.reply(stanza);
    reply:tag("response", { xmlns = NS }):text(response_text);
    session.send(reply);

    log("info", "Admin %s executed: %s -> %s", username, cmd_text,
        response_text:sub(1, 200));

    return true;
end);

-- ---------------------------------------------------------------------------
-- Ban enforcement: reject banned users at login
-- ---------------------------------------------------------------------------

module:hook("resource-bind", function(event)
    local session = event.session;
    if not session or not session.username then return; end

    local conn = open_db();
    if not conn then return; end

    local stmt = conn:prepare(
        "SELECT banned_until, ban_reason FROM thrive_bans WHERE username = ?"
    );
    stmt:execute(session.username);
    local row = stmt:fetch(true);

    if row then
        local today = os.date("%Y-%m-%d");
        if row.banned_until >= today then
            session:close({
                condition = "not-authorized",
                text = "You are banned until " .. row.banned_until
                    .. (row.ban_reason and (": " .. row.ban_reason) or "."),
            });
            return true; -- Prevent bind.
        else
            -- Ban has expired — clean it up.
            local del = conn:prepare("DELETE FROM thrive_bans WHERE username = ?");
            del:execute(session.username);
        end
    end
end, 100); -- Priority 100: run before most other handlers.

-- ---------------------------------------------------------------------------
-- Module loaded
-- ---------------------------------------------------------------------------

module:log("info", "mod_thrive_admin loaded (shutdown_timeout=%ds, db=%s)",
    shutdown_timeout, db_path);
