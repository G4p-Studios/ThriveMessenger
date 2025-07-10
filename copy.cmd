@echo off
robocopy sounds\default tmsg.dist\sounds\default /E
robocopy sounds\galaxia tmsg.dist\sounds\galaxia /E
robocopy sounds\skype tmsg.dist\sounds\skype /E
copy srv.conf tmsg.dist