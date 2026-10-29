#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PYTHON:-python3}
if [ -x "$APP_DIR/.venv/bin/python" ]; then
    VENV="$APP_DIR/.venv"
else
    VENV="$APP_DIR/.venv-linux"
fi
BIN_DIR="$HOME/.local/bin"
STOCK_LINK="$BIN_DIR/stock"
LEGACY_LINK="$BIN_DIR/stockboard"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    printf '%s\n' "Python 3 was not found. Install it with: sudo apt install python3 python3-venv python3-pip" >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' "StockBoard requires Python 3.10 or newer." >&2
    exit 1
fi

if ! "$PYTHON" -m venv "$VENV"; then
    printf '%s\n' "Could not create the virtual environment. Install python3-venv and try again." >&2
    exit 1
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

mkdir -p "$BIN_DIR"
chmod +x "$APP_DIR/stock"
chmod +x "$APP_DIR/stockboard"
for COMMAND_LINK in "$STOCK_LINK" "$LEGACY_LINK"; do
    if [ -e "$COMMAND_LINK" ] && [ ! -L "$COMMAND_LINK" ]; then
        printf '%s\n' "$COMMAND_LINK already exists and is not a symlink; leaving it unchanged." >&2
        exit 1
    fi
    if [ -L "$COMMAND_LINK" ] && [ "$(readlink -f -- "$COMMAND_LINK")" != "$APP_DIR/${COMMAND_LINK##*/}" ]; then
        printf '%s\n' "$COMMAND_LINK points to another installation; leaving it unchanged." >&2
        exit 1
    fi
    ln -sfn "$APP_DIR/${COMMAND_LINK##*/}" "$COMMAND_LINK"
done

printf '\n%s\n' "StockBoard installed. Try: stock start"
case ":${PATH:-}:" in
    *":$BIN_DIR:"*) ;;
    *) printf '%s\n' "Add this to ~/.profile if the command is not found: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac