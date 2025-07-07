# Thrive Messenger, chat like it's 2005!

## Introduction

Thrive Messenger is an instant messaging service that aims to bring back the speed, simplicity, accessibility, fun and excitement of instant messengers from the 90s and 2000s, such as AIM, ICQ and MSN/Windows Live Messenger. It is not a revival project like [Escargot Chat](https://escargot.chat). Rather, it is an entirely new IM platform built from scratch. We're not reviving any old services, we're reviving the vibe of those services.

Part of the Thrive Essentials suite of software from G4p Studios, Thrive Messenger features a simplistic, accessible user interface that is easy to navigate and use for those who rely on screen readers such as [NVDA](https://nvaccess.org) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). It achieves this with clear labels for UI elements, buttons, text fields, checkboxes etc, and keyboard friendly navigation, allowing the user to use their arrow keys and the tab key to move around menus and other parts of the UI.

Thrive Messenger is open source, meaning anyone is free to download, view and modify the source code, and decentralised, meaning anyone can host their own Thrive Messenger Server. This is the client readme, however, so hosting servers won't be discussed here. Server code and readme is coming soon.

* * *

## How to use Thrive Messenger

### Accounts

In order to use Thrive Messenger, you will need a Thrive Messenger account. All you need for an account is a username, which will be used to add you as a contact, and a strong password that nobody can guess.

You can create an account from the Thrive Messenger login dialog, or a server admin can create an account for you.

### Running from source

Note: these instructions are for running Thrive Messenger on Windows.

1. Make sure you have [Git for Windows](https://gitforwindows.org) and [Python](https://www.python.org/downloads/windows/) installed.
2. Press Windows + R, type cmd, and press Enter.
3. Clone the GitHub repository.

    ```
    git clone https://github.com/G4p-Studios/ThriveMessenger.git
    ```

4. Navigate to the ThriveMessenger directory.

    ```
    cd ThriveMessenger
    ```

5. Create a Python virtual environment. This gives you an isolated workspace for installing the libraries Thrive Messenger needs without affecting your main Python install.

    ```
    python -m venv venv
    ```

6. Activate the virtual environment.

    ```
    venv\scripts\activate
    ```

7. To avoid errors when installing libraries, update pip, setuptools and wheel.

    ```
    python -m pip install --upgrade pip setuptools wheel
    ```

8. Install the required libraries.

    ```
    pip install -r requirements.txt
    ```

9. Finally, run the tmsg.py file. If all is well, you should see the Thrive Messenger login screen.

    ```
    python tmsg.py
    ```

### Compiling

If you wish to compile a binary, with your virtual environment activated, run the compile script.

```
compile.cmd
```

### Running compiled

If you don't feel like fighting with Git and Python, a pre-compiled release is provided.

1. [Download the latest Thrive Messenger release](https://github.com/G4p-Studios/ThriveMessenger/releases/latest/download/thrive_messenger.zip)
2. Extract the zip file to a location of your choice.
3. Navigate to where you extracted the zip file and run tmsg.exe. If your system isn't set up to show file extensions, the filename will just show as tmsg.

As long as you have a PC with Windows 7 or higher, an internet connection and a working sound card, this release should work just fine.

### Login

Logging into your Thrive Messenger account is as simple as logging into your computer's user account.

1.  Enter your username at the Thrive Messenger login screen.
2.  Tab to the password field and enter your Thrive Messenger password.
3.  Optionally, check the boxes to remember your credentials and log in automatically.
4.  Press Enter to log into Thrive Messenger. A sound will play to tell you that you're logged in.

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

You can start an IM conversation with a contact simply by pressing Enter on them in the contact list. Once you do, you will land on a text field where you can type your message. Pressing Enter will send the message, and pressing Shift + Enter will type a new line. Pressing Shift + Tab once will take you to a list of all messages sent and received in the chat. Use the up and down arrow keys to navigate this list. To get out of the chat and go back to the main Thrive Messenger window, simply press the Escape key. Don't worry, your chat history will be saved. It will only be cleared when you log out or exit the program.

* * *

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

## The srv.conf file

The srv.conf file controls what server and port the Thrive Messenger client connects to. If you have your own Thrive Messenger server up, or you have one that you like to use, you can simply open srv.conf in your text editor of choice, such as Notepad++, and modify the server hostname and port to point to your desired server.

The default server is msg.thecubed.cc, running on port 2005.