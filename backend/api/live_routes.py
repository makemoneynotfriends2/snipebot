"""
Live Bot API Routes  –  V2
===========================
GET    /api/live/stream          → Server-Sent Events (SSE) – neue Artikel in Echtzeit
POST   /api/live/start           → Bot starten (mit Profile-IDs oder Legacy-Config)
POST   /api/live/stop            → Bot stoppen
GET    /api/live/status          → Aktueller Bot-Status
DELETE /api/live/history         → Erkannte Artikel-History löschen

GET    /api/live/settings        → Globale Einstellungen (Discord, API Key)
POST   /api/live/settings        → Einstellungen speichern
"""

import json
import os
import time
import logging
from flask import Blueprint, jsonify, request, Response, stream_with_context
from scrapers.vinted_live import VintedLivePoller
from config import Config

logger  = logging.getLogger(__name__)
live_bp = Blueprint('live', __name__)

# Globaler Poller – einmal erzeugen, läuft im Hintergrund
_poller = VintedLivePoller()

# In-Memory Match-History (letzte 200 Artikel)
_history: list = []
MAX_HISTORY     = 200

# Pfad zur Settings-Datei
_SETTINGS_FILE = os.path.join(
    os.path.dirname(__file__), '..', '..', 'data', 'settings.json'
)


# ── Settings-Helpers ──────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(data: dict):
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
    with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── SSE Stream ────────────────────────────────────────────────────

@live_bp.route('/stream', methods=['GET'])
def stream():
    """
    Server-Sent Events: sendet neue gefilterte Artikel sobald sie ankommen.
    Frontend verbindet sich mit EventSource('/api/live/stream').
    """
    def generate():
        yield _sse({'type': 'connected', 'message': 'Stream aktiv'})

        last_heartbeat = time.time()
        while True:
            try:
                event = _poller.queue.get(timeout=20)
                if event.get('type') == 'item':
                    item = event['item']
                    entry = {
                        **item,
                        'profile_name':  event.get('profile_name', ''),
                        'vision_matched': event.get('vision_matched', False),
                    }
                    _history.insert(0, entry)
                    if len(_history) > MAX_HISTORY:
                        _history.pop()
                    yield _sse({'type': 'item', 'item': entry})
                    last_heartbeat = time.time()
            except Exception:
                pass

            if time.time() - last_heartbeat > 18:
                status = _poller.get_status()
                yield _sse({'type': 'heartbeat', 'status': status['status'], 'ts': time.time()})
                last_heartbeat = time.time()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Bot Control ───────────────────────────────────────────────────

@live_bp.route('/start', methods=['POST'])
def start_bot():
    """
    Bot starten – V2: profile_ids übergeben, Backend lädt die Profile aus der DB.

    Body-Option A (bevorzugt, V2):
        {"profile_ids": [1, 2, 3]}

    Body-Option B (Legacy, V1-kompatibel):
        {"brands": ["adidas"], "keywords": [...], "price_from": 5, "price_to": 80, "interval": 15}

    Globale Settings (discord_webhook, anthropic_api_key) werden automatisch
    aus der Settings-Datei geladen und dem Poller übergeben.
    """
    data     = request.json or {}
    settings = _load_settings()

    # ── V2: Profile aus Datenbank laden ──────────────────────────
    if 'profile_ids' in data:
        from database.models import WatchProfile

        profile_ids = [int(pid) for pid in data['profile_ids']]
        if not profile_ids:
            return jsonify({'error': 'Mindestens ein Profil angeben'}), 400

        profiles_raw = WatchProfile.query.filter(WatchProfile.id.in_(profile_ids)).all()
        if not profiles_raw:
            return jsonify({'error': 'Keine passenden Profile gefunden'}), 404

        profiles = []
        for p in profiles_raw:
            # Trainingsbilder-Pfade sammeln (absoluter Pfad via Config)
            img_dir     = os.path.join(Config.TRAINING_IMAGES_DIR, str(p.id))
            image_paths = []
            if os.path.isdir(img_dir):
                image_paths = [
                    os.path.join(img_dir, fn)
                    for fn in os.listdir(img_dir)
                    if fn.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
                ]

            profiles.append({
                'id':              p.id,
                'name':            p.name,
                'brands':          json.loads(p.brands_json   or '[]'),
                'keywords':        json.loads(p.keywords_json or '[]'),
                'price_from':      p.price_from,
                'price_to':        p.price_to,
                'interval':        p.interval or 15,
                'image_paths':     image_paths,
                'discord_webhook': p.discord_webhook or '',
            })

        merged_config = {
            'discord_webhook':   settings.get('discord_webhook', ''),
            'anthropic_api_key': settings.get('anthropic_api_key', ''),
        }

        _poller.start(profiles=profiles, merged_config=merged_config)
        vision_active  = bool(settings.get('anthropic_api_key')) and any(p['image_paths'] for p in profiles)
        discord_active = bool(settings.get('discord_webhook')) or any(p['discord_webhook'] for p in profiles)
        return jsonify({
            'success':       True,
            'profile_count': len(profiles),
            'vision_active': vision_active,
            'discord_active': discord_active,
            'status':        'running',
        })

    # ── V1 Legacy: brands/keywords direkt übergeben ───────────────
    brands = data.get('brands', [])
    if not brands or not any(b.strip() for b in brands):
        return jsonify({'error': 'Mindestens eine Marke oder profile_ids angeben'}), 400

    legacy_profile = {
        'id':          0,
        'name':        'Manuelle Suche',
        'brands':      [b.strip() for b in brands if b.strip()],
        'keywords':    [k.strip() for k in data.get('keywords', []) if k.strip()],
        'price_from':  data.get('price_from'),
        'price_to':    data.get('price_to'),
        'interval':    max(int(data.get('interval', 15)), 10),
        'image_paths': [],
    }
    merged_config = {
        'discord_webhook':   settings.get('discord_webhook', ''),
        'anthropic_api_key': settings.get('anthropic_api_key', ''),
    }

    _poller.start(profiles=[legacy_profile], merged_config=merged_config)
    return jsonify({'success': True, 'status': 'running'})


@live_bp.route('/stop', methods=['POST'])
def stop_bot():
    _poller.stop()
    return jsonify({'success': True, 'status': 'stopped'})


@live_bp.route('/status', methods=['GET'])
def bot_status():
    return jsonify(_poller.get_status())


# ── History ───────────────────────────────────────────────────────

@live_bp.route('/history', methods=['GET'])
def get_history():
    limit = int(request.args.get('limit', 50))
    return jsonify(_history[:limit])


@live_bp.route('/history', methods=['DELETE'])
def clear_history():
    _history.clear()
    return jsonify({'success': True})


# ── Rejected Items ─────────────────────────────────────────────────

@live_bp.route('/rejected', methods=['GET'])
def get_rejected():
    """Abgelehnte Artikel abrufen (wurden gefunden aber nicht weitergeleitet)."""
    limit = int(request.args.get('limit', 50))
    return jsonify(_poller.get_rejected(limit=limit))


# ── Globale Einstellungen ─────────────────────────────────────────

@live_bp.route('/settings', methods=['GET'])
def get_settings():
    """Globale Einstellungen abrufen."""
    s = _load_settings()
    # API Key nur als bool zurückgeben, nicht den Wert selbst
    return jsonify({
        'discord_webhook':      s.get('discord_webhook', ''),
        'anthropic_api_key':    s.get('anthropic_api_key', ''),
        'has_anthropic_key':    bool(s.get('anthropic_api_key', '').strip()),
    })


@live_bp.route('/settings', methods=['POST'])
def save_settings():
    """Globale Einstellungen speichern."""
    data = request.json or {}
    s    = _load_settings()

    if 'discord_webhook' in data:
        s['discord_webhook'] = data['discord_webhook'].strip()
    if 'anthropic_api_key' in data:
        s['anthropic_api_key'] = data['anthropic_api_key'].strip()

    try:
        _save_settings(s)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
