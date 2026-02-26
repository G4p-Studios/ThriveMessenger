-- mod_auth_thrive.lua
-- Custom Prosody authentication provider for Thrive Messenger.
--
-- Wraps Prosody's native SCRAM-SHA-1 credential storage while adding
-- transparent fallback to legacy argon2 password hashes from the old
-- Thrive server.  When a user authenticates via their old argon2
-- password, the module:
--   1. Verifies the hash using a small Python helper (argon2-cffi).
--   2. Re-hashes the password as SCRAM-SHA-1 in Prosody's accounts store.
--   3. Deletes the legacy hash row.
-- Future logins for that user use native SCRAM credentials.
--
-- The module advertises the PLAIN SASL mechanism (over TLS, which is
-- required by our config).  PLAIN is needed because SCRAM is a
-- challenge-response protocol where the server never sees the plaintext
-- password -- so it cannot verify against an argon2 hash.  Once ALL
-- legacy hashes have been migrated, you can switch back to
-- authentication = "internal_hashed" for SCRAM-only auth.
--
-- Configuration (prosody.cfg.lua):
--   authentication = "thrive"
--   thrive_db_path = "/var/lib/prosody/thrive.db"
--   thrive_argon2_verify = "/etc/prosody/thrive-modules/verify_argon2.py"

local new_sasl = require "util.sasl".new;
local hashes   = require "util.hashes";
local uuid     = require "util.uuid";
local json     = require "util.json";
local DBI      = require "DBI";

local host     = module.host;
local log      = module._log;

-- Prosody's built-in per-user data store (same one internal_hashed uses).
local accounts = module:open_store("accounts");

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

local db_path       = module:get_option_string("thrive_db_path", "thrive.db");
local verify_script = module:get_option_string("thrive_argon2_verify",
    "/etc/prosody/thrive-modules/verify_argon2.py");
local scram_iterations = module:get_option_number("scram_iteration_count", 4096);

-- ---------------------------------------------------------------------------
-- Legacy password helpers (argon2 hashes in thrive_legacy_passwords)
-- ---------------------------------------------------------------------------

local function open_legacy_db()
    local ok, conn = pcall(DBI.Connect, "SQLite3", db_path);
    if not ok or not conn then return nil; end
    conn:autocommit(true);

    -- Ensure the table exists (idempotent).
    local stmt = conn:prepare([[
        CREATE TABLE IF NOT EXISTS thrive_legacy_passwords (
            username      TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    ]]);
    if stmt then stmt:execute(); end

    return conn;
end

local function get_legacy_hash(username)
    local conn = open_legacy_db();
    if not conn then return nil; end
    local stmt = conn:prepare(
        "SELECT password_hash FROM thrive_legacy_passwords WHERE username = ?"
    );
    if not stmt then conn:close(); return nil; end
    stmt:execute(username);
    local row = stmt:fetch();
    conn:close();
    return row and row[1] or nil;
end

local function delete_legacy_hash(username)
    local conn = open_legacy_db();
    if not conn then return; end
    local stmt = conn:prepare(
        "DELETE FROM thrive_legacy_passwords WHERE username = ?"
    );
    if stmt then stmt:execute(username); end
    conn:close();
end

--- Verify a password against an argon2 hash via the Python helper.
-- The helper reads a JSON file containing { "hash": "...", "password": "..." }
-- and prints "ok" or "fail".
local function verify_argon2(stored_hash, password)
    -- Write credentials to a temp file (avoids exposing passwords in process list).
    local tmpfile = os.tmpname();
    local f = io.open(tmpfile, "w");
    if not f then
        log("error", "Could not create temp file for argon2 verification");
        return false;
    end
    f:write(json.encode({ hash = stored_hash, password = password }));
    f:close();

    local pipe = io.popen("python3 " .. verify_script .. " " .. tmpfile .. " 2>/dev/null");
    local output = "";
    if pipe then
        output = (pipe:read("*a") or ""):gsub("%s+", "");
        pipe:close();
    end
    os.remove(tmpfile);

    return output == "ok";
end

-- ---------------------------------------------------------------------------
-- SCRAM-SHA-1 credential helpers
-- ---------------------------------------------------------------------------

--- Generate and store SCRAM-SHA-1 credentials for a user.
local function store_scram(username, password)
    local salt = uuid.generate();
    local salted_password = hashes.scram_Hi_sha1(password, salt, scram_iterations);
    local client_key   = hashes.hmac_sha1(salted_password, "Client Key");
    local stored_key   = hashes.sha1(client_key);
    local server_key   = hashes.hmac_sha1(salted_password, "Server Key");

    -- base64-encode binary values (same format as internal_hashed).
    local b64 = require "util.encodings".base64.encode;
    return accounts:set(username, {
        iteration_count = scram_iterations,
        salt            = b64(salt),
        stored_key      = b64(stored_key),
        server_key      = b64(server_key),
    });
end

--- Verify a plaintext password against stored SCRAM-SHA-1 credentials.
-- Returns true if the password matches, false otherwise.
local function verify_scram(credentials, password)
    if not credentials or not credentials.stored_key then return false; end

    local b64_decode = require "util.encodings".base64.decode;
    local salt       = b64_decode(credentials.salt);
    local iterations = credentials.iteration_count;
    local expected   = b64_decode(credentials.stored_key);

    local salted_password = hashes.scram_Hi_sha1(password, salt, iterations);
    local client_key = hashes.hmac_sha1(salted_password, "Client Key");
    local stored_key = hashes.sha1(client_key);

    return stored_key == expected;
end

-- ---------------------------------------------------------------------------
-- Auth provider
-- ---------------------------------------------------------------------------

local provider = {};

function provider.test_password(username, password)
    -- 1. Try native SCRAM credentials.
    local credentials = accounts:get(username);
    if credentials and credentials.stored_key then
        if verify_scram(credentials, password) then
            return true;
        end
    end

    -- 2. Fall back to legacy argon2 hash.
    local legacy_hash = get_legacy_hash(username);
    if legacy_hash then
        if verify_argon2(legacy_hash, password) then
            -- Lazy rehash: store as SCRAM so future logins are native.
            log("info", "Migrating password for '%s' from argon2 to SCRAM", username);
            store_scram(username, password);
            delete_legacy_hash(username);
            return true;
        end
    end

    return false;
end

function provider.user_exists(username)
    local account = accounts:get(username);
    if account then return true; end
    -- User might only have a legacy hash (not yet logged in post-migration).
    return get_legacy_hash(username) ~= nil;
end

function provider.set_password(username, password)
    store_scram(username, password);
    delete_legacy_hash(username);  -- Clear legacy entry if any.
    return true;
end

function provider.create_user(username, password)
    return store_scram(username, password);
end

function provider.delete_user(username)
    delete_legacy_hash(username);
    return accounts:set(username, nil);
end

function provider.get_sasl_handler()
    -- Offer PLAIN mechanism only.  Connection is TLS-encrypted
    -- (c2s_require_encryption = true), so PLAIN is safe here.
    -- PLAIN is required during the migration period because SCRAM never
    -- exposes the plaintext password to the server, making it impossible
    -- to verify against legacy argon2 hashes.
    local profile = {
        plain_test = function(_, auth_username, auth_password, auth_realm)
            return provider.test_password(auth_username, auth_password), true;
        end,
    };
    return new_sasl(host, profile);
end

module:provides("auth", provider);

module:log("info",
    "mod_auth_thrive loaded (legacy argon2 migration enabled, verify=%s)",
    verify_script);
