-- mod_thrive_directory.lua
-- Prosody module for Thrive Messenger user directory.
--
-- Responds to custom IQ queries (urn:thrive:directory) with a list of
-- all registered users and their online/offline/status info.
--
-- Usage from client:
--   <iq type="get" to="msg.thecubed.cc">
--     <query xmlns="urn:thrive:directory"/>
--   </iq>
--
-- Response:
--   <iq type="result">
--     <directory xmlns="urn:thrive:directory">
--       <user>
--         <username>alice</username>
--         <status>online</status>
--         <admin>false</admin>
--       </user>
--       ...
--     </directory>
--   </iq>

local st = require "util.stanza";
local usermanager = require "core.usermanager";
local jid_join = require "util.jid".join;
local DBI = require "DBI";

local log = module._log;
local host = module.host;

local NS = "urn:thrive:directory";

-- Database path shared with mod_thrive_admin and mod_thrive_reset.
local db_path = module:get_option_string("thrive_db_path", "thrive.db");

--- Check if a user is currently online by looking at their sessions.
local function is_user_online(username)
    local user_sessions = prosody.hosts[host] and prosody.hosts[host].sessions
        and prosody.hosts[host].sessions[username];
    if user_sessions and next(user_sessions.sessions) then
        return true;
    end
    return false;
end

--- Get the status text for an online user.
local function get_user_status(username)
    local user_sessions = prosody.hosts[host] and prosody.hosts[host].sessions
        and prosody.hosts[host].sessions[username];
    if not user_sessions then
        return "offline";
    end
    -- Check each session's presence for show/status.
    for _, session in pairs(user_sessions.sessions or {}) do
        if session.presence then
            local show = session.presence:get_child_text("show");
            local status = session.presence:get_child_text("status");
            if status and status ~= "" then
                return status;
            elseif show then
                local show_map = {
                    away = "away",
                    xa = "away",
                    dnd = "busy",
                    chat = "online",
                };
                return show_map[show] or "online";
            end
            return "online";
        end
    end
    return "offline";
end

--- Check if a user is an admin (static config OR dynamic thrive_admins table).
local function is_admin(username)
    -- Static admins from prosody.cfg.lua
    if usermanager.is_admin(jid_join(username, host), host) then
        return true;
    end
    -- Dynamic admins from thrive_admins table (shared with mod_thrive_admin).
    local ok, conn = pcall(DBI.Connect, "SQLite3", db_path);
    if not ok or not conn then return false; end
    conn:autocommit(true);
    local stmt = conn:prepare("SELECT 1 FROM thrive_admins WHERE username = ?");
    if not stmt then conn:close(); return false; end
    stmt:execute(username);
    local row = stmt:fetch();
    conn:close();
    return row ~= nil;
end

-- ---------------------------------------------------------------------------
-- IQ handler: urn:thrive:directory (get)
-- ---------------------------------------------------------------------------

module:hook("iq/host", function(event)
    local stanza = event.stanza;
    local query = stanza:get_child("query", NS);
    if not query then return; end
    if stanza.attr.type ~= "get" then return; end

    -- Only authenticated users can request the directory.
    if not event.origin.username then
        event.origin.send(st.error_reply(stanza, "auth", "not-authorized",
            "You must be logged in to request the user directory."));
        return true;
    end

    -- Enumerate all registered users.
    local reply = st.reply(stanza);
    local directory = reply:tag("directory", { xmlns = NS });

    for username in usermanager.users(host) do
        local online = is_user_online(username);
        local status = online and get_user_status(username) or "offline";
        local admin = is_admin(username);

        directory:tag("user")
            :tag("username"):text(username):up()
            :tag("status"):text(status):up()
            :tag("admin"):text(admin and "true" or "false"):up()
        :up();
    end

    event.origin.send(reply);
    return true;
end);

-- ---------------------------------------------------------------------------
-- Module loaded
-- ---------------------------------------------------------------------------

module:log("info", "mod_thrive_directory loaded");
