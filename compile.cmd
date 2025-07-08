@echo off
python versionfile.py
pyinstaller --windowed --version-file=vdata.txt --upx-dir=C:\UPX tmsg.py
robocopy sounds\default dist\tmsg\sounds\default /E
robocopy sounds\skype dist\tmsg\sounds\skype /E
copy srv.conf dist\tmsg