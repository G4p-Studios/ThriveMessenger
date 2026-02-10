@echo off
uv run python versionfile.py
uv run pyinstaller --clean --noconfirm --windowed --upx-dir=C:\UPX --version-file=vdata.txt --name tmsg main.py