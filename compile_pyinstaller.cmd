@echo off
uv run python versionfile.py
uv run pyinstaller --clean --noconfirm --windowed --upx-dir=C:\UPX --version-file=vdata.txt --name tmsg --hidden-import plyer.platforms --hidden-import plyer.platforms.win --hidden-import plyer.platforms.win.notification main.py