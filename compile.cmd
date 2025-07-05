@echo off
python versionfile.py
pyinstaller --windowed --version-file=vdata.txt --upx-dir=C:\UPX tmsg.py