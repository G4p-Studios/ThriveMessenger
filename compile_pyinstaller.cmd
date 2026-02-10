@echo off
uv run python versionfile.py
uv run pyinstaller --windowed --upx-dir=C:\UPX --version-file=vdata.txt tmsg.py