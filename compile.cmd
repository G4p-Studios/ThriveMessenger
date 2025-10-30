@echo off
python versionfile.py
pyinstaller --windowed --upx-dir=C:\UPX --version-file=vdata.txt tmsg.py