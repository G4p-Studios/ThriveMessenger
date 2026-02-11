@echo off
robocopy sounds\default dist\tmsg\sounds\default /E
robocopy sounds\galaxia dist\tmsg\sounds\galaxia /E
robocopy sounds\skype dist\tmsg\sounds\skype /E
copy client.conf dist\tmsg