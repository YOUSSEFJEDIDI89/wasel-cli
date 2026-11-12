#!/usr/bin/env bash
# Wasel installer — works on Termux, Linux, macOS and Git Bash (WSL).
# No pip, no compiler, no root. Pure Python standard library.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${WASEL_BIN_DIR:-$HOME/.local/bin}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required." >&2
  echo "  Termux:  pkg install python" >&2
  echo "  Debian:  sudo apt install python3" >&2
  exit 1
fi

PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 - <<'PY' || { echo "error: Python 3.8+ is required." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
PY

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/wasel" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="$SRC" python3 -m wasel "\$@"
EOF
chmod +x "$BIN_DIR/wasel"

echo "✔ wasel installed → $BIN_DIR/wasel"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "Add it to your PATH:"
    echo "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    if [ -n "${PREFIX:-}" ]; then
      echo "  (Termux detected: the line above also works in Termux)"
    fi
    ;;
esac

echo
echo "Try it now — no API key required:"
echo "  $BIN_DIR/wasel -p 'list the files here and summarise them'"
echo
echo "Unlock bigger free models:  wasel keys set gemini <YOUR_KEY>"
