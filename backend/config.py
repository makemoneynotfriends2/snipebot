import os

# Basis-Verzeichnis des Projekts
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Config:
    # ----------------------------------------------------------------
    # eBay API (optional – trägt du hier ein, bekommst du stabilere Daten)
    # Kostenlos registrieren: https://developer.ebay.com/
    # ----------------------------------------------------------------
    EBAY_APP_ID = os.environ.get('EBAY_APP_ID', '')

    # eBay Site: 77 = ebay.de (Deutschland)
    EBAY_SITE_ID = '77'

    # ----------------------------------------------------------------
    # Datenbank
    # ----------------------------------------------------------------
    DATABASE_PATH = os.path.join(BASE_DIR, 'data', 'marktanalyse.db')
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{DATABASE_PATH}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ----------------------------------------------------------------
    # Trainingsbilder (absoluter Pfad – verhindert Pfad-Auflösungs-Bugs)
    # ----------------------------------------------------------------
    TRAINING_IMAGES_DIR = os.path.join(BASE_DIR, 'data', 'training_images')

    # ----------------------------------------------------------------
    # Scraping
    # ----------------------------------------------------------------
    REQUEST_TIMEOUT = 20           # Sekunden bis Timeout (höher wegen Pagination)
    MAX_RESULTS_EBAY = 50          # Maximale eBay-Ergebnisse pro Suche
    MAX_RESULTS_VINTED = 1000      # Vinted: bis zu 1000 Items (10+ Seiten × 96)

    # ----------------------------------------------------------------
    # Anthropic Vision API (optional – für KI-Bilderkennung im Snipe Bot)
    # Hol dir einen Key: https://console.anthropic.com/
    # Alternativ: in den App-Einstellungen (Admin → Einstellungen) eintragen
    # ----------------------------------------------------------------
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

    # ----------------------------------------------------------------
    # App
    # ----------------------------------------------------------------
    DEBUG = False
    PORT = 5001
    HOST = '127.0.0.1'
    SECRET_KEY = 'marktanalyse-secret-2024'
