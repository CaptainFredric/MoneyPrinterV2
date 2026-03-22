#!/usr/bin/env bash
# scripts/install_mp_shortcut.sh
# Installs an `mp` command shortcut in ~/.zshrc and ~/.bashrc.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHORTCUT_LINE='alias mp="bash '$ROOT_DIR'/scripts/phone_post.sh"'

install_in_file() {
  local rc_file="$1"
  touch "$rc_file"
  if grep -Fq "$SHORTCUT_LINE" "$rc_file"; then
    echo "[mp] Already present in $rc_file"
  else
    {
      echo ""
      echo "# MoneyPrinter phone shortcut"
      echo "$SHORTCUT_LINE"
    } >> "$rc_file"
    echo "[mp] Added shortcut to $rc_file"
  fi
}

install_in_file "$HOME/.zshrc"
install_in_file "$HOME/.bashrc"

echo ""
echo "✅ Installed shortcut: mp"
echo "Reload shell with one of:"
echo "  source ~/.zshrc"
echo "  source ~/.bashrc"
echo ""
echo "Examples:"
echo "  mp status"
echo "  mp check niche_launch_1"
echo "  mp detach niche_launch_1"
echo "  mp health"
