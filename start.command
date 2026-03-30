#!/bin/bash
# ────────────────────────────────────────────────
#  Marktanalyse Tool – Starter
#  Doppelklick genügt.
# ────────────────────────────────────────────────

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Virtualenv aktivieren (falls vorhanden)
if [ -d "$DIR/venv" ]; then
    source "$DIR/venv/bin/activate"
    PYTHON="$DIR/venv/bin/python"
else
    # Kein venv – systemweites Python nutzen
    if command -v python3 &>/dev/null; then
        PYTHON=python3
    else
        PYTHON=python
    fi

    # Pakete installieren falls nötig
    echo "→ Prüfe Abhängigkeiten…"
    "$PYTHON" -m pip install --quiet -r "$DIR/requirements.txt" 2>/dev/null || \
    "$PYTHON" -m pip install --quiet -r "$DIR/requirements.txt" --break-system-packages 2>/dev/null || true
fi

# App starten
echo "→ Starte Marktanalyse Tool…"
cd "$DIR/backend"
"$PYTHON" app.py
