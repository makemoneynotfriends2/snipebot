"""
Marktanalyse Tool – Flask App
==============================
Einstiegspunkt. Startet den lokalen Server und öffnet den Browser.
"""

import os
import sys
import logging
import threading
import webbrowser

from flask import Flask, send_from_directory
from flask_cors import CORS

# Damit Python die Module im gleichen Ordner findet
sys.path.insert(0, os.path.dirname(__file__))

from config           import Config
from database.db      import db
from api.routes       import api_bp
from api.live_routes  import live_bp
from api.profile_routes import profile_bp

# Logging konfigurieren
logging.basicConfig(
    level   = logging.INFO,
    format  = '%(asctime)s  %(levelname)-8s  %(name)s – %(message)s',
    datefmt = '%H:%M:%S',
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')


def create_app() -> Flask:
    app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
    app.config['SECRET_KEY']                  = Config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI']     = Config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    CORS(app)
    db.init_app(app)
    app.register_blueprint(api_bp,      url_prefix='/api')
    app.register_blueprint(live_bp,     url_prefix='/api/live')
    app.register_blueprint(profile_bp,  url_prefix='/api/profiles')

    @app.route('/')
    def index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    # Datenbank-Tabellen anlegen (idempotent)
    with app.app_context():
        os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
        db.create_all()
        # Migration: neue Spalten hinzufügen falls noch nicht vorhanden
        from sqlalchemy import text
        with db.engine.connect() as conn:
            for col_sql in [
                "ALTER TABLE watch_profiles ADD COLUMN discord_webhook VARCHAR(500) DEFAULT ''",
            ]:
                try:
                    conn.execute(text(col_sql))
                    conn.commit()
                except Exception:
                    pass  # Spalte existiert bereits
        logger.info(f"Datenbank bereit: {Config.DATABASE_PATH}")

    return app


def _open_browser():
    webbrowser.open(f'http://{Config.HOST}:{Config.PORT}')


if __name__ == '__main__':
    app = create_app()

    # Browser nach kurzem Delay öffnen (Werkzeug braucht einen Moment)
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        threading.Timer(1.5, _open_browser).start()

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║     🤖 Snipe Bot & Marktanalyse      ║")
    print("  ╠══════════════════════════════════════╣")
    print(f"  ║  URL: http://{Config.HOST}:{Config.PORT}        ║")
    print("  ║  Stoppen: Ctrl + C                   ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    app.run(
        host  = Config.HOST,
        port  = Config.PORT,
        debug = Config.DEBUG,
    )
