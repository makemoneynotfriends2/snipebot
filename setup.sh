#!/bin/bash
# ────────────────────────────────────────────────
#  Marktanalyse Tool – Ersteinrichtung
#  Einmal ausführen, danach nur noch start.command
# ────────────────────────────────────────────────

set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║    🔍 Marktanalyse Tool – Setup      ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Python prüfen
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "  ❌  Python nicht gefunden."
    echo "      Bitte Python 3.8+ installieren: https://www.python.org/"
    exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓  Python $PYVER gefunden"

# Virtualenv anlegen
echo "  →  Erstelle virtuelle Umgebung…"
"$PYTHON" -m venv "$DIR/venv"

# Aktivieren
source "$DIR/venv/bin/activate"

# Dependencies installieren
echo "  →  Installiere Abhängigkeiten…"
pip install --quiet --upgrade pip
pip install --quiet -r "$DIR/requirements.txt"

echo ""
echo "  ✅  Setup abgeschlossen!"
echo ""
echo "  Nächster Schritt:"
echo "  → Doppelklick auf  start.command"
echo ""
