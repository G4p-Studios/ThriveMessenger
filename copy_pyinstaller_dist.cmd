@echo off
robocopy sounds\default dist\thrive_messenger\sounds\default /E
robocopy sounds\galaxia dist\thrive_messenger\sounds\galaxia /E
robocopy sounds\skype dist\thrive_messenger\sounds\skype /E
copy client.conf dist\thrive_messenger