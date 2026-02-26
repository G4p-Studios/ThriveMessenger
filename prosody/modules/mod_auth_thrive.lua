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
    module:log("debug", "verify_argon2: writing temp file");
    -- Write credentials to a temp file (avoids exposing passwords in process list).
    local tmpfile = os.tmpname();
    local f = io.open(tmpfile, "w");
    if not f then
        module:log("error", "Could not create temp file for argon2 verification");
        return false;
    end
    f:write(json.encode({ hash = stored_hash, password = password }));
    f:close();

    -- Use timeout to prevent blocking Prosody's event loop indefinitely.
    local cmd = "timeout 5 python3 " .. verify_script .. " " .. tmpfile .. " 2>&1";
    module:log("debug", "verify_argon2: running command: %s", cmd);
    local pipe = io.popen(cmd);
    local output = "";
    if pipe then
        output = (pipe:read("*a") or ""):gsub("%s+", "");
        pipe:close();
    end
    module:log("debug", "verify_argon2: output = '%s'", output);
    os.remove(tmpfile);

    return output == "ok";
end

-- ---------------------------------------------------------------------------
-- SCRAM-SHA-1 credential helpers
-- ---------------------------------------------------------------------------

--- Hex-encode a binary string (for SCRAM credential storage).
local function to_hex(s)
    local t = {};
    for i = 1, #s do
        t[i] = string.format("%02x", s:byte(i));
    end
    return table.concat(t);
end

--- Generate and store SCRAM-SHA-1 credentials for a user.
-- Matches Prosody's internal_hashed format: salt is a raw UUID string,
-- stored_key and server_key are lowercase hex.
local function store_scram(username, password)
    local salt = uuid.generate();
    local salted_password = hashes.scram_Hi_sha1(password, salt, scram_iterations);
    local client_key   = hashes.hmac_sha1(salted_password, "Client Key");
    local stored_key   = hashes.sha1(client_key, true);  -- true = hex output
    local server_key   = to_hex(hashes.hmac_sha1(salted_password, "Server Key"));

    return accounts:set(username, {
        iteration_count = scram_iterations,
        salt            = salt,         -- raw UUID string
        stored_key      = stored_key,   -- hex
        server_key      = server_key,   -- hex
    });
end

--- Verify a plaintext password against stored SCRAM-SHA-1 credentials.
-- Prosody's internal_hashed stores: salt as raw UUID string, stored_key
-- and server_key as lowercase hex.  We compare hex directly.
local function verify_scram(credentials, password)
    if not credentials or not credentials.stored_key then return false; end
    if not credentials.salt or not credentials.iteration_count then
        module:log("warn", "SCRAM credentials incomplete (salt=%s, iterations=%s)",
            tostring(credentials.salt), tostring(credentials.iteration_count));
        return false;
    end

    local salted_password = hashes.scram_Hi_sha1(
        password, credentials.salt, credentials.iteration_count);
    local client_key = hashes.hmac_sha1(salted_password, "Client Key");
    local computed   = hashes.sha1(client_key, true);  -- true = hex output

    return computed == credentials.stored_key;
end

-- ---------------------------------------------------------------------------
-- Auth provider
-- ---------------------------------------------------------------------------

local provider = {};

function provider.test_password(username, password)
    module:log("debug", "test_password called for '%s'", username);

    -- 1. Try native SCRAM credentials.
    module:log("debug", "Checking SCRAM credentials for '%s'", username);
    local ok, credentials = pcall(accounts.get, accounts, username);
    if not ok then
        module:log("error", "accounts:get('%s') failed: %s", username, tostring(credentials));
        return false;
    end
    if credentials and credentials.stored_key then
        module:log("debug", "Found SCRAM credentials for '%s', verifying", username);
        local scram_ok, scram_result = pcall(verify_scram, credentials, password);
        if not scram_ok then
            module:log("error", "verify_scram('%s') error: %s", username, tostring(scram_result));
            return false;
        end
        if scram_result then
            module:log("debug", "SCRAM verification succeeded for '%s'", username);
            return true;
        end
        module:log("debug", "SCRAM verification failed for '%s'", username);
    else
        module:log("debug", "No SCRAM credentials for '%s'", username);
    end

    -- 2. Fall back to legacy argon2 hash.
    module:log("debug", "Checking legacy argon2 hash for '%s'", username);
    local hash_ok, legacy_hash = pcall(get_legacy_hash, username);
    if not hash_ok then
        module:log("error", "get_legacy_hash('%s') failed: %s", username, tostring(legacy_hash));
        return false;
    end
    if legacy_hash then
        module:log("debug", "Found legacy hash for '%s', verifying via argon2", username);
        local a2_ok, a2_result = pcall(verify_argon2, legacy_hash, password);
        if not a2_ok then
            module:log("error", "verify_argon2('%s') error: %s", username, tostring(a2_result));
            return false;
        end
        if a2_result then
            -- Lazy rehash: store as SCRAM so future logins are native.
            module:log("info", "Migrating password for '%s' from argon2 to SCRAM", username);
            local store_ok, store_err = pcall(store_scram, username, password);
            if not store_ok then
                module:log("error", "store_scram('%s') failed: %s", username, tostring(store_err));
            end
            local del_ok, del_err = pcall(delete_legacy_hash, username);
            if not del_ok then
                module:log("error", "delete_legacy_hash('%s') failed: %s", username, tostring(del_err));
            end
            return true;
        end
        module:log("debug", "Argon2 verification failed for '%s'", username);
    else
        module:log("debug", "No legacy hash for '%s'", username);
    end

    module:log("debug", "All auth methods exhausted for '%s' — denying", username);
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
            module:log("debug", "SASL plain_test callback for '%s'", auth_username);
            local ok, result = pcall(provider.test_password, auth_username, auth_password);
            if not ok then
                module:log("error", "test_password crashed for '%s': %s", auth_username, tostring(result));
                return false, true;
            end
            module:log("debug", "SASL plain_test result for '%s': %s", auth_username, tostring(result));
            return result, true;
        end,
    };
    return new_sasl(host, profile);
end

module:provides("auth", provider);

module:log("info",
    "mod_auth_thrive loaded (legacy argon2 migration enabled, verify=%s)",
    verify_script);
