@echo off
python versionfile.py
pyinstaller --windowed --version-file=vdata.txt --upx-dir=C:\UPX tmsg.py
robocopy . dist\tmsg contact_online.wav login.wav logout.wav send.wav receive.wav srv.conf