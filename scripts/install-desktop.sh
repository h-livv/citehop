#!/usr/bin/env bash
# Install a GNOME/KDE app-menu launcher for this machine only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

mkdir -p "$APP_DIR" "$ICON_DIR"
install -m 644 "$ROOT/src/citehop/assets/citehop.svg" "$ICON_DIR/citehop.svg"
install -m 644 "$ROOT/packaging/citehop.desktop" "$APP_DIR/citehop.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Installed $APP_DIR/citehop.desktop"
echo "Citehop is in the app menu. Pin it from there if it is not on the dash."
