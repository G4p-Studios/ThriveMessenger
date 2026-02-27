# Thrive Messenger, chat like it's 2005!

## Introduction

Thrive Messenger is an instant messaging service that aims to bring back the speed, simplicity, fun and excitement of instant messengers from the 90s and 2000s, such as AIM, ICQ and MSN/Windows Live Messenger. It is not a revival project like [Escargot Chat](https://escargot.chat). Rather, it is an entirely new IM platform built from scratch. We're not reviving any old services, we're reviving the vibe of those services.

Part of the Thrive Essentials suite of software from G4p Studios, Thrive Messenger features a simplistic, accessible user interface that is easy to navigate and use for those who rely on screen readers such as [NVDA](https://nvaccess.org) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). It achieves this with clear labels for UI elements, buttons, text fields, checkboxes etc, optional auto reading of new messages, and keyboard friendly navigation, allowing the user to use their arrow keys and the tab key to move around menus and other parts of the UI. Back in the day, this level of accessibility either required screen reader vendors to optimise their readers to work with the IM software, or third party scripts and screen reader add-ons from the blind community. Thrive Messenger is designed to be accessible by default, so the program works with the screen reader, not the other way round. Just how the 2000s should have been.

Thrive Messenger is open source, meaning anyone is free to download, view and modify the source code, and decentralised, meaning anyone can host their own Thrive Messenger Server.

* * *

## Client usage

### Accounts

In order to use Thrive Messenger, you will need a Thrive Messenger account. All you need for an account is a username which will be used to add you as a contact, an optional email address, and a strong password that nobody can guess.

You can create an account from the Thrive Messenger login dialog, or a server admin can create an account for you.

Please note: for both security and convenience, if the server you're using has SMTP enabled (see below), you are required to enter a valid email address when creating an account. This is so your account can be verified by email and you can easily reset your password if you forget it.

### Running from source

Note: these instructions are for running Thrive Messenger on Windows.
The Thrive Messenger client uses UV for dependency management. UV is a fast and reliable dependency and virtual environment manager for Python written in Rust. If you're a rust programmer or if you've ever compiled a Rust program, UV is basically Cargo for Python. It can even install Python for you if you don't have it already.

1. Make sure you have [Git for Windows](https://gitforwindows.org) installed.
2. Press Windows + R, type powershell, and press Enter.
3. Run the following command in PowerShell to install UV.

    ```
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

4. Optionally, use UV to install Python.

    ```
    uv python install 3.13
    ```
5. Clone the GitHub repository.

    ```
    git clone https://github.com/G4p-Studios/ThriveMessenger.git
    ```

6. Navigate to the ThriveMessenger directory.

    ```
    cd ThriveMessenger
    ```

7. Run install.cmd to install the required libraries.

    ```
    .\install.cmd
    ```

9. Finally, run the program. If all is well, you should see the Thrive Messenger login screen.

    ```
    .\run.cmd
    ```
Note: you can also navigate to the ThriveMessenger folder in Windows Explorer and double click or press Enter on the install.cmd and run.cmd files to run them.

### Compiling

If you wish to compile a binary, run the appropriate compile script. Thrive Messenger can either be compiled with nuitka or PyInstaller.

    ```
    compile_nuitka.cmd
    ```

    ```
    compile_pyinstaller.cmd
    ```

### Compiling on macOS (Intel + Apple Silicon)

The repo includes a macOS build script that produces a `.app` zipped archive:

```bash
chmod +x scripts/build_macos.sh
scripts/build_macos.sh
```

This writes output archives to `dist-macos/`.

For automated dual-architecture builds, run the GitHub Actions workflow:
`Build macOS Desktop`.
It produces:

- `thrive_messenger-macos-x86_64.zip`
- `thrive_messenger-macos-arm64.zip`

### Running compiled

If you don't feel like fighting with UV and Python, a pre-compiled release is provided.

1. [Download the latest Thrive Messenger release](https://github.com/G4p-Studios/ThriveMessenger/releases/latest/download/thrive_messenger.zip)
2. Extract the zip file to a location of your choice.
3. Navigate to where you extracted the zip file and run tmsg.exe. If your system isn't set up to show file extensions, the filename will just show as tmsg.

Alternatively, you can [download the Thrive Messenger installer](https://github.com/G4p-Studios/ThriveMessenger/releases/latest/download/thrive_messenger_installer.exe) and run through the on-screen prompts to install the program.

As long as you have a PC with Windows 7 or higher, an internet connection and a working sound card, this release should work just fine.

### Login

Logging into your Thrive Messenger account is as simple as logging into your computer's user account.

1.  Enter your username at the Thrive Messenger login screen.
2.  Tab to the password field and enter your Thrive Messenger password.
3.  Optionally, check the boxes to remember your credentials and log in automatically.
4.  Click Login or Press Alt + L to log into Thrive Messenger. A sound will play to tell you that you're logged in.

### The Thrive Messenger UI

When you log into Thrive Messenger, you will land on your contact list. Of course, if your account is brand new, you won't have any contacts to chat with. This list view will show you the name of each contact, as well as their online status. You can navigate your contact list with the up and down arrow keys. Using the Tab key will allow you to navigate the rest of the UI.

*   The block button, accessible with Alt + B, lets you block the focused contact in the list so they can't message you. This is useful if they are being spammy or abusive.
*   The add Contact button, Alt + A, will let you add a new contact. Simply click this button, enter the username of the contact you wish to add, then press Enter.
*   You can either focus on a contact in the list and press Enter to start a chat with them, or tab to and click the Start Chat (Alt + S) button.
* Alt + F will allow you to send a file to the focused contact.
*   You can delete the focused contact with the Delete button or Alt + D.
*   The Use Server Side Commands (Alt + V) button will allow you to perform various server side commands; more on these later.
*   The logout (Alt + O) and exit (Alt + X) buttons are self explanatory.
* The server info button (Alt + I) will show information about the server you're currently logged into.
* Alt + U will allow you to set an online status that your contacts will see. You can choose from a list of preset statuses, such as online, offline and busy, or you can choose a custom one and type a personal message. Server owners can customize the character limit for custom statuses via max_status_length, so check that you have enough characters before you start setting System of a Down lyrics as your status.
* Alt P will check for updates to the program and allow you to auto download them.
* Pressing Alt F4 will minimize the client to the system tray, ready for you to receive messages. Simply double click or press Enter on the Thrive Messenger system tray item to bring it back up.

### Sending and receiving messages

All messages are end-to-end encrypted using OMEMO (XEP-0384). Encryption keys are generated automatically on first login and exchanged via PEP/PubSub — no manual key setup is needed. Both sides of a conversation must have logged in at least once for encryption to work.

You can start an IM conversation with a contact simply by pressing Enter on them in the contact list. Once you do, you will land on a text field where you can type your message. Pressing Enter will send the message, and pressing Shift + Enter will type a new line. Pressing Shift + Tab once will take you to a checkbox which will allow you to save a permanent log of your chat with the current contact, stored in Documents/ThriveMessenger/chats/<contact>. Pressing Shift + Tab again will show a list of all messages sent and received in the chat. Use the up and down arrow keys to navigate this list. To get out of the chat and go back to the main Thrive Messenger window, simply press the Escape key.

### File transfer

As well as sending standard text messages, users can also send files to each other. To send a file, simply highlight the contact you want to send the file to and press Alt + F or click the send file button. A dialog will open where you can choose the file(s) you wish to send. Multiple files can be selected at once. The file is uploaded to the server and a download link is sent to the recipient, who will be prompted to accept or decline. Received files are stored in Documents/ThriveMessenger/files.
Note: server owners might place file size limits on uploads; see below on how to configure this.

### Server side commands

If you see (Admin) beside a contact's online status, it means they are classed as a server admin and can perform server side commands from the client. This is what the aforementioned Use Server Side Commands button is for. Clicking the button will bring up a dialog much like the one that appears when you start a chat with a contact. You will auto focus on the command input field. To run a command, simply type it into the field and press Enter.

Each server side command must start with a forward slash (/). The following server side commands are available.

*   /admin username: makes the specified user an admin.
*   /unadmin username: takes the admin user's powers away.
*   /create username password [email]: creates a user account with the specified username and password, with an optional email address.
*   /del user: deletes the specified user's account from the server.
*   /ban username date reason: bans the specified user from the server until the specified date (in MM/DD/YYYY format) for the specified reason. Wanna ban someone permanently? Just do what they do on Xbox Live and ban them until December 31st, 9999! Ha ha ha!
*   /unban username: Unbans the specified user.
* /banfile username type date reason: bans the user from sending a certain type of file until a given date. For example, /banfile doglover05 exe 12/31/9999 sending malware. Using a star (*) in the type argument will ban the user from sending files altogether. Using the command without a date will result in a permanent file ban.
* /unbanfile username type: Lifts the user's file ban for the given type. If no type is given, all file bans for the user will be lifted.
* /alert message: Sends a Windows Live style alert message to all online users. For example, /alert The server is about to be shut down for maintenance.
*   /restart: Restarts the server after a brief delay.
*   /exit: Shuts down the server.

Shift Tabbing once from the command input field will show a list of outputs for the commands you've run.

Those of you familiar with IRC will know that slash commands were very much inspired by IRC's server and channel operator commands.

### Sound packs

Sound packs allow you to customise the various sounds Thrive Messenger uses for its events, such as sending and receiving messages, contacts coming online, and logging into the server.
Thrive Messenger ships with 3 sound packs by default.

* Default: Contains a collection of sounds made by blind UK-based musician Andre Louis.
* Galaxia: contains the Galaxia sounds used in the [Thrive Mastodon client](https://github.com/G4p-Studios/Thrive).
* Skype: contains sounds from Skype versions 7 and earlier.

#### Creating sound packs

Structurally, a sound pack is simply a folder with a collection of wave files inside it. To create a sound pack, you will need the following 9 files:

* contact_online
* contact_offline
* login
* logout
* receive
* send
* file_receive
* file_send
* file_error

Make a folder inside Thrive Messenger's sounds folder and paste these files into that folder to create your custom sound pack.

#### Changing sound packs

With the Thrive Messenger client open and logged in, follow these steps to change your sound pack.

1. Press Alt + T to access the settings dialog.
2. Choose a sound pack from the sound pack dropdown menu with the up and down arrow keys.
3. Either press Enter or click the apply button to apply your settings.

### Server manager

Thrive Messenger supports connecting to multiple servers. You can manage your server list from the login screen by clicking the Servers button (Alt + S). From there you can add new servers, remove servers, and choose which server is your primary.

The default server is msg.thecubed.cc.

You can also control update sources in `client.conf`:

```
[updates]
feed_url = https://im.tappedin.fm/updates/latest.json
preferred_repo = Raywonder/ThriveMessenger
fallback_repos = G4p-Studios/ThriveMessenger
```

- `feed_url` is optional. If set, the client checks your hosted feed first.
- If feed lookup fails, the client falls back to GitHub repos in order.
- This allows your custom channel and upstream compatibility at the same time.

### Cron-ready update feed sync

This repo includes `srv/scripts/sync_update_feed.sh` to publish a JSON update feed from GitHub Releases.

Example cron (every 5 minutes):

```
*/5 * * * * /path/to/ThriveMessenger/srv/scripts/sync_update_feed.sh Raywonder/ThriveMessenger /var/www/im.tappedin.fm/updates/latest.json >/var/log/thrive-update-feed.log 2>&1
```

### Auto reading of messages

In the settings dialog accessible with Alt + T, there is an option to have new messages automatically read aloud by your screen reader. This is turned off by default for sighted users.

### The user directory

The user directory, Alt + Y, allows you to quickly find and chat with anyone on your Thrive Messenger server. The user directory is divided into 4 tabs, allowing you to choose between seeing online users, offline users, admins, and the server's entire userbase.

### Offline chats

Think you might have missed messages when you were offline? No problem. When you log back in, the program will check the server for held messages, and if it sees any, will show a dialog asking if you want to see the messages. Clicking yes will show a list of users who sent you messages. Simple press enter on a user to open the chat and see their messages.

### Non-contact chats

Someone sends you a message and they aren't in your contacts. You get to chatting with them for a bit, but then you close the chat to do other things and realise you forgot to add your newfound friend as a contact. This is where the non-contact conversations view comes in. Just like with offline chats, when you open this dialog with Alt C, you will see a list of users who aren't in your contacts but have been chatting with you, allowing you to easily return to the chat and add them if you wish.

* * *

## Server usage

Thrive Messenger uses [Prosody](https://prosody.im), an open-source XMPP server, as its backend. All messages are sent over XMPP with optional OMEMO end-to-end encryption. The following instructions are for Linux.

### Installing Prosody

1. Install Prosody using your distribution's package manager.

    ```
    sudo apt update
    sudo apt install prosody prosody-modules lua-dbi-sqlite3
    ```

2. Clone this repository.

    ```
    git clone https://github.com/G4p-Studios/ThriveMessenger
    ```

3. Deploy the Thrive custom modules and Prosody configuration. A deployment script is provided that copies modules, the config, and sets up the systemd restart override.

    ```
    sudo bash ThriveMessenger/prosody/deploy_modules.sh
    ```

    Alternatively, you can deploy manually:

    ```
    sudo mkdir -p /etc/prosody/thrive-modules
    sudo cp ThriveMessenger/prosody/modules/*.lua /etc/prosody/thrive-modules/
    sudo cp ThriveMessenger/prosody/modules/verify_argon2.py /etc/prosody/thrive-modules/
    ```

4. Copy the example Prosody configuration and edit it to match your domain.

    ```
    sudo cp ThriveMessenger/prosody/prosody.cfg.lua /etc/prosody/prosody.cfg.lua
    sudo nano /etc/prosody/prosody.cfg.lua
    ```

    At a minimum, update:
    * The `VirtualHost` line to your domain (e.g. `VirtualHost "chat.example.com"`)
    * The `ssl` certificate and key paths
    * The `admins` list with your admin JID (e.g. `admins = { "yourname@chat.example.com" }`)

5. Allow the XMPP ports through the firewall.

    ```
    sudo ufw allow 5222
    sudo ufw allow 5269
    sudo ufw allow 5280
    ```

6. Start Prosody.

    ```
    sudo systemctl enable prosody
    sudo systemctl start prosody
    ```

### Setting up TLS

Thrive Messenger requires TLS encryption for all connections. Use Let's Encrypt to get free certificates.

1. Install Certbot. [This guide from the Electronic Frontier Foundation](https://certbot.eff.org/instructions?ws=other&os=pip) covers the setup.

2. Generate certificates for your domain.

    ```
    sudo certbot certonly --standalone -d chat.example.com
    ```

3. Import the certificates into Prosody.

    ```
    sudo prosodyctl cert import /etc/letsencrypt/live
    ```

    Alternatively, update the `ssl` section in `prosody.cfg.lua` to point to your certificate files directly.

4. Restart Prosody.

    ```
    sudo systemctl restart prosody
    ```

### Creating user accounts

You can create user accounts from the server command line or from the admin console in the client.

```
sudo prosodyctl register username chat.example.com password
```

Or, from the admin console in the client: `/create username password`

### Allowing your server to send emails

You can optionally enable SMTP on your server to allow account verification and password reset codes to be sent to users by email. Users that create accounts on SMTP-enabled servers are required to supply a valid email address for these features to work.

Edit your `prosody.cfg.lua` and fill in the SMTP settings:

```lua
thrive_smtp_server   = "smtp.example.com"
thrive_smtp_port     = 587
thrive_smtp_user     = "noreply@example.com"
thrive_smtp_password = "your-smtp-password"
thrive_smtp_from     = "noreply@example.com"
```

Then restart Prosody for the changes to take effect.

### File transfer limits

File transfers use HTTP Upload (XEP-0363). You can configure limits in `prosody.cfg.lua`:

* `http_file_share_size_limit`: Maximum file size in bytes. For example, to set a 2 GB limit:

    ```lua
    http_file_share_size_limit = 2000000000
    ```

* `http_file_share_expires_after`: How long uploaded files are kept, in seconds. Default is 7 days (604800).

* `http_file_share_daily_quota`: Maximum total upload size per user per day, in bytes.

### Running bots

Thrive Messenger supports AI-powered bots that connect to the server as regular XMPP users. Bots are powered by a local [Ollama](https://ollama.com) instance.

1. Register bot accounts on the server.

    ```
    sudo prosodyctl register assistant-bot chat.example.com botpassword123
    sudo prosodyctl register helper-bot chat.example.com botpassword456
    ```

2. Edit `bot_config.ini` with your server details and bot passwords.

3. Run the bot process.

    ```
    python3 thrive_bot.py
    ```

    Each `[bot:name]` section in the config file spawns a bot that logs in and responds to messages via Ollama.

### Migrating from the legacy server

If you are migrating from the old custom Python server (`srv/server.py`), a migration script is provided to transfer your user accounts, contacts, bans, and admin list to Prosody. A wrapper script (`migrate.sh`) simplifies the process.

1. Make sure Prosody is installed and running with `authentication = "internal_hashed"` (the default in the provided config).

2. Preview the migration with a dry run first.

    ```
    bash migrate.sh -d
    ```

    The script auto-detects your XMPP domain from `prosody.cfg.lua` and locates the legacy `thrive.db` in the repo. You can override these with environment variables: `DOMAIN=example.com OLD_DB=/path/to/thrive.db bash migrate.sh`

3. Run the migration for real. The script automatically backs up Prosody's data directory and thrive.db before making any changes, and writes a JSON manifest recording every action taken.

    ```
    bash migrate.sh
    ```

4. If accounts are already migrated but you need to re-run just the contacts migration:

    ```
    bash migrate.sh -c
    ```

5. If something goes wrong, roll back using a manifest file.

    ```
    bash migrate.sh -r migration-manifest-20260226-153012.json
    ```

    This deletes all created Prosody accounts, removes roster files, and clears the thrive.db tables that were populated. A pre-migration backup is also available in the backup directory for manual restoration if needed.

6. Deploy the custom modules (including the auth module) and fix file ownership.

    ```
    sudo bash prosody/deploy_modules.sh
    ```

7. Switch authentication to the Thrive provider in `prosody.cfg.lua`.

    ```lua
    authentication = "thrive"
    ```

8. Restart Prosody.

    ```
    sudo systemctl restart prosody
    ```

9. Users keep their existing passwords. The custom auth module (`mod_auth_thrive`) transparently verifies against the old argon2 hashes on first login and re-hashes to Prosody's native SCRAM format. No password reset is needed. Make sure `argon2-cffi` is installed on the server: `pip3 install argon2-cffi`.

10. Once all users have logged in at least once, you can optionally switch `authentication` in `prosody.cfg.lua` from `"thrive"` back to `"internal_hashed"` for SCRAM-only auth.

* * *

## Credits

Sounds in the default sound pack were created by Andre Louis.

* [Andre Louis's website](http://www.andrelouis.com/)
* [Check out Andre on YouTube](https://www.youtube.com/channel/UCqtMRZeGSGWMAy0AX31K0MQ)
* Listen to the [StroongeCast podcast](http://www.andrelouis.com/stroongecast), hosted by Andre and his wife Kirsten Louis.
* [Andre on Mastodon (personal)](https://universeodon.com/@FreakyFwoof)
* [Andre on Mastodon (music)](https://mastodonmusic.social/@Onj)
