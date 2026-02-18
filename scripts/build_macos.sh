#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

APP_NAME="Thrive Messenger"
DIST_NAME="thrive_messenger"
OUT_DIR="${ROOT_DIR}/dist-macos"
ARCH_LABEL="${1:-$(uname -m)}"
VENV_DIR="${ROOT_DIR}/.venv-build"
PYTHON_BIN="${THRIVE_PYTHON_BIN:-python3}"

${PYTHON_BIN} -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip
python -m pip install \
  "pyinstaller>=6.18.0" \
  "keyring>=25.7.0" \
  "plyer>=2.1.0" \
  "wxPython>=4.2.5"

rm -rf build dist "${OUT_DIR}"
mkdir -p "${OUT_DIR}"

pyinstaller \
  --clean \
  --noconfirm \
  --windowed \
  --name "${APP_NAME}" \
  --osx-bundle-identifier "fm.tappedin.thrivemessenger" \
  --add-data "client.conf:." \
  --add-data "sounds:sounds" \
  main.py

APP_PATH="dist/${APP_NAME}.app"
ZIP_PATH="${OUT_DIR}/${DIST_NAME}-macos-${ARCH_LABEL}.zip"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: ${APP_PATH} was not created" >&2
  exit 1
fi

ditto -c -k --sequesterRsrc --keepParent "${APP_PATH}" "${ZIP_PATH}"
echo "Created ${ZIP_PATH}"
