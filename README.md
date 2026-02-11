# Thrive Messenger, chat like it's 2005!

## Introduction

Thrive Messenger is an instant messaging service that aims to bring back the speed, simplicity, accessibility, fun and excitement of instant messengers from the 90s and 2000s, such as AIM, ICQ and MSN/Windows Live Messenger. It is not a revival project like [Escargot Chat](https://escargot.chat). Rather, it is an entirely new IM platform built from scratch. We're not reviving any old services, we're reviving the vibe of those services.

Part of the Thrive Essentials suite of software from G4p Studios, Thrive Messenger features a simplistic, accessible user interface that is easy to navigate and use for those who rely on screen readers such as [NVDA](https://nvaccess.org) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). It achieves this with clear labels for UI elements, buttons, text fields, checkboxes etc, and keyboard friendly navigation, allowing the user to use their arrow keys and the tab key to move around menus and other parts of the UI.

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

### Running compiled

If you don't feel like fighting with UV and Python, a pre-compiled release is provided.

1. [Download the latest Thrive Messenger release](https://github.com/G4p-Studios/ThriveMessenger/releases/latest/download/thrive_messenger.zip)
2. Extract the zip file to a location of your choice.
3. Navigate to where you extracted the zip file and run tmsg.exe. If your system isn't set up to show file extensions, the filename will just show as tmsg.

As long as you have a PC with Windows 7 or higher, an internet connection and a working sound card, this release should work just fine.

### Login

Logging into your Thrive Messenger account is as simple as logging into your computer's user account.

1.  Enter your username at the Thrive Messenger login screen.
2.  Tab to the password field and enter your Thrive Messenger password.
3.  Optionally, check the boxes to remember your credentials and log in automatically.
4.  Click Login or Press Alt + L to log into Thrive Messenger. A sound will play to tell you that you're logged in.

### The Thrive Messenger UI

When you log into Thrive Messenger, you will land on your contact list. Of course, if your account is brand new, you won't have any contacts to chat with. This list view will show you the name of each contact, as well as their online status, either online or offline. You can navigate your contact list with the up and down arrow keys. Using the Tab key will allow you to navigate the rest of the UI.

*   The block button, accessible with Alt + B, lets you block the focused contact in the list so they can't message you. This is useful if they are being spammy or abusive.
*   The add Contact button, Alt + A, will let you add a new contact. Simply click this button, enter the username of the contact you wish to add, then press Enter.
*   You can either focus on a contact in the list and press Enter to start a chat with them, or tab to and click the Start Chat (Alt + S) button.
*   You can delete the focused contact with the Delete button or Alt + D.
*   The Use Server Side Commands (Alt + V) button will allow you to perform various server side commands; more on these later.
*   The logout (Alt + O) and exit (Alt + X) buttons are self explanatory.

Pressing Alt F4 will minimize the client to the system tray, ready for you to receive messages. Simply double click or press Enter on the Thrive Messenger system tray item to bring it back up.

### Sending and receiving messages

You can start an IM conversation with a contact simply by pressing Enter on them in the contact list. Once you do, you will land on a text field where you can type your message. Pressing Enter will send the message, and pressing Shift + Enter will type a new line. Pressing Shift + Tab once will take you to a checkbox which will allow you to save a permanent log of your chat with the current contact, stored in Documents/ThriveMessenger/chats/<contact>. Pressing Shift + Tab again will show a list of all messages sent and received in the chat. Use the up and down arrow keys to navigate this list. To get out of the chat and go back to the main Thrive Messenger window, simply press the Escape key.

* * *

## Server usage

### Running the server

The Thrive Messenger server is also written in Python, but it is standard library, meaning it does not require any external dependencies to run.
Note: the server will technically run on both Windows and Linux, but the following instructions are optimised for Linux, so you will need a Linux machine either in the cloud or on your local network.

1. If for some reason git is not installed on your machine, install it with these commands, substituting apt for your distro's package manager.

    ```
    sudo apt update
    sudo apt install git
    ```

2. Clone this repository.

    ```
    git clone https://github.com/G4p-Studios/ThriveMessenger
    ```

3. Navigate to the srv directory inside the repo.

    ```
    cd ThriveMessenger/srv
    ```

4. Make a screen session so you're not constantly tied to the terminal when running the server.

    ```
    screen -S thrive
    ```

5. Ensure the server's port is allowed through the firewall.

    ```
    sudo ufw allow 2005
    ```


6. Finally, runn the server.

    ```
    python3 server.py
    ```

6. Detach yourself from the screen session by pressing Control + A, then press D.

Please note: the server will run in unencrypted mode by default, meaning data sent from the client will be sent in plain text. This should only be used for testing servers. In a production environment, you must use valid SSL certificates from a trusted certificate authority such as Let's Encrypt.

### Encrypting the server

Assuming you have a domain or hostname, do the following to enable SSL on your Thrive Messenger server.

1. Use a tool like Certbot to generate your Let's Encrypt certificates. [This guide from the Electronic Frontier Foundation](https://certbot.eff.org/instructions?ws=other&os=pip) should help with this.
2. In the srv directory of the Thrive Messenger repo, run this command to edit the server's srv.conf file.

    ```
    nano srv.conf
    ```

3. Add these lines to the end of the file, replacing example.com with your actual domain name.

    ```
    certfile=/etc/letsencrypt/live/example.com/fullchain.pem
    keyfile=/etc/letsencrypt/live/example.com/privkey.pem
    ```

4. Start the server. It should now say that there's a secure server listening on port <port>.

### Allowing your server to send emails

You can optionally enable SMTP on your Thrive Messenger server to allow account verification and password reset codes to be sent to users by email. Users that create accounts on SMTP-enabled servers are required to supply a valid email address for these features to work.
To allow your server to send emails, simply add these files to the end of your server's srv.conf file, replacing values with those given to you by your email provider.

    ```
    [smtp]
    enabled=true
    server=host.tld
    port=587
    email=your_username@host.tld
    password=your_password
    ```

## Server side commands

If you see (Admin) beside a contact's online status, it means they are classed as a server admin and can perform server side commands from the client. This is what the aforementioned Use Server Side Commands button is for. Clicking the button will bring up a dialog much like the one that appears when you start a chat with a contact. You will auto focus on the command input field. To run a command, simply type it into the field and press Enter.

Each server side command must start with a forward slash (/). The following server side commands are available.

*   /admin username: Makes the specified user an admin.
*   /create username password: Creates a user account with the specified username and password.
*   /del user: Deletes the specified user's Thrive Messenger account from the server.
*   /ban username date reason: Bans the specified user from Thrive Messenger until the specified date (in MM/DD/YYYY format) for the specified reason. Wanna ban someone permanently? Just do what they do on Xbox Live and ban them until December 31st, 9999! Ha ha ha!
*   /unban username: Unbans the specified user.
*   /unadmin username: Removes the specified user's admin so they can't run server side commands anymore.
* /alert message: Sends a Windows Live style alert message to all online users. For example, /alert The server is about to be shut down for maintenance.
*   /exit: Shuts down the Thrive Messenger server.

Shift Tabbing once from the command input field will show a list of outputs for the commands you've run.

Those of you familiar with IRC will know that this feature was very much inspired by the concept of server and channel operator commands.

* * *

## The client.conf file

The client.conf file controls what server and port the Thrive Messenger client connects to. If you have your own Thrive Messenger server up, or you have one that you like to use, you can simply open client.conf in your text editor of choice, such as Notepad++, and modify the server hostname and port to point to your desired server.

The default server is msg.thecubed.cc, running on port 2005.

* * *

## Sound packs

Sound packs allow you to customise the various sounds Thrive Messenger uses for its events, such as sending and receiving messages, contacts coming online, and logging into the server.
Thrive Messenger ships with 3 sound packs by default.

* Default: Contains a collection of sounds made by blind UK-based musician Andre Louis.
* Galaxia: contains the Galaxia sounds used in the [Thrive Mastodon client](https://github.com/G4p-Studios/Thrive).
* Skype: contains sounds from Skype versions 7 and earlier.

### Creating sound packs

Structurally, a sound pack is simply a folder with a collection of wave files inside of it. To create a sound pack, you will need the following 6 files:

* contact_online.wav
* contact_offline.wav
* login.wav
* logout.wav
* receive.wav
* send.wav

Make a folder inside Thrive Messenger's sounds folder and paste these files into that folder to create your custom sound pack.

### Changing sound packs

With the Thrive Messenger client open and logged in, follow these steps to change your sound pack.

1. Press Alt + T to access the settings dialog.
2. Choose a sound pack from the sound pack dropdown menu with the up and down arrow keys.
3. Either press Enter or click the apply button to apply your settings.

* * *

## Credits

Sounds in the default sound pack were created by Andre Louis.

* [Andre Louis's website](http://www.andrelouis.com/)
* [Check out Andre on YouTube](https://www.youtube.com/channel/UCqtMRZeGSGWMAy0AX31K0MQ)
* Listen to the [StroongeCast podcast](http://www.andrelouis.com/stroongecast), hosted by Andre and his wife Kirsten Louis.
* [Andre on Mastodon (personal)](https://universeodon.com/@FreakyFwoof)
* [Andre on Mastodon (music)](https://mastodonmusic.social/@Onj)