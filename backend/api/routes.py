"""
API-Routen (Flask Blueprint)
============================
GET  /api/search?q=Nike        → Marktanalyse (Produktgruppen + Hot Items)
GET  /api/history               → Letzte Suchanfragen
GET  /api/saved                 → Watchlist
POST /api/saved                 → Artikel speichern
DELETE /api/saved/<id>          → Artikel löschen
GET  /api/settings              → Einstellungen lesen
POST /api/settings              → Einstellungen speichern (z.B. eBay API Key)
GET  /api/status                → Health-Check
"""

import os
import json
import logging
import concurrent.futures
from flask import Blueprint, jsonify, request

from scrapers.ebay    import EbayScraper
from scrapers.vinted  import VintedScraper
from analysis.scoring import analyze
from analysis.vision  import VisionAnalyzer
from database.db      import db
from database.models  import SearchHistory, SavedItem, PriceSnapshot, VisionCache
from config           import Config

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__)

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'settings.json')


def _load_settings() -> dict:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_settings(data: dict):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# Scrapers – eBay App ID aus Settings laden falls vorhanden
_settings        = _load_settings()
_ebay_app_id     = _settings.get('ebay_app_id') or Config.EBAY_APP_ID
_anthropic_key   = _settings.get('anthropic_api_key', '')

_ebay_scraper    = EbayScraper(app_id=_ebay_app_id, timeout=Config.REQUEST_TIMEOUT)
_vinted_scraper  = VintedScraper(timeout=Config.REQUEST_TIMEOUT)
_vision_analyzer = VisionAnalyzer(api_key=_anthropic_key)


# ------------------------------------------------------------------
# Suche
# ------------------------------------------------------------------

@api_bp.route('/search', methods=['GET'])
def search():
    query = (request.args.get('q', '') or '').strip()
    if not query:
        return jsonify({'error': 'Suchbegriff fehlt'}), 400

    logger.info(f"Suche: '{query}'")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        ebay_fut   = pool.submit(_ebay_scraper.search_sold_items, query, Config.MAX_RESULTS_EBAY)
        vinted_fut = pool.submit(_vinted_scraper.search, query, Config.MAX_RESULTS_VINTED)
        ebay_items   = [i for i in (ebay_fut.result()   or []) if i.get('title')]
        vinted_items = [i for i in (vinted_fut.result() or []) if i.get('title')]

    all_items = vinted_items + ebay_items
    analysis  = analyze(all_items, query=query)

    # In DB speichern
    try:
        db.session.add(SearchHistory(
            query        = query,
            result_count = len(all_items),
            ebay_count   = len(ebay_items),
            vinted_count = len(vinted_items),
        ))
        _save_price_snapshot(query, ebay_items, vinted_items)
        db.session.commit()
    except Exception as e:
        logger.warning(f"DB-Fehler: {e}")
        db.session.rollback()

    return jsonify({
        'query':          query,
        'total':          len(all_items),
        'ebay_count':     len(ebay_items),
        'vinted_count':   len(vinted_items),
        'ebay_has_api':   bool(_ebay_scraper.app_id),
        'product_groups': analysis['product_groups'],
        'hot_items':      analysis['hot_items'],
        'market_summary': analysis['market_summary'],
        'trending_now':   analysis.get('trending_now', []),
    })


# ------------------------------------------------------------------
# Suchverlauf
# ------------------------------------------------------------------

@api_bp.route('/history', methods=['GET'])
def get_history():
    rows = SearchHistory.query.order_by(SearchHistory.timestamp.desc()).limit(20).all()
    return jsonify([r.to_dict() for r in rows])

@api_bp.route('/history', methods=['DELETE'])
def clear_history():
    SearchHistory.query.delete()
    db.session.commit()
    return jsonify({'success': True})


# ------------------------------------------------------------------
# Watchlist
# ------------------------------------------------------------------

@api_bp.route('/saved', methods=['GET'])
def get_saved():
    items = SavedItem.query.order_by(SavedItem.saved_at.desc()).all()
    return jsonify([i.to_dict() for i in items])

@api_bp.route('/saved', methods=['POST'])
def save_item():
    data = request.json or {}
    if not data.get('title'):
        return jsonify({'error': 'Kein Titel'}), 400

    existing = SavedItem.query.filter_by(
        source=data.get('source'),
        external_id=str(data.get('id', ''))
    ).first()
    if existing:
        return jsonify({'error': 'Bereits gespeichert', 'item': existing.to_dict()}), 409

    item = SavedItem(
        source      = data.get('source', ''),
        external_id = str(data.get('id', '')),
        title       = data.get('title', ''),
        price       = data.get('price', 0),
        currency    = data.get('currency', 'EUR'),
        brand       = data.get('brand', ''),
        score       = data.get('score', 0),
        score_label = data.get('score_label', ''),
        url         = data.get('url', ''),
        image       = data.get('image', ''),
        condition   = data.get('condition', ''),
        size        = data.get('size', ''),
        likes       = data.get('likes', 0),
        notes       = data.get('notes', ''),
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict()), 201

@api_bp.route('/saved/<int:item_id>', methods=['DELETE'])
def delete_saved(item_id):
    item = SavedItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})


# ------------------------------------------------------------------
# Einstellungen
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# KI-Bildanalyse
# ------------------------------------------------------------------

@api_bp.route('/enhance', methods=['POST'])
def enhance():
    """
    Analysiert Produktbilder mit KI und gibt verbesserte Produkttypen zurück.
    Body: { "items": [...], "query": "Markenname" }
    Gibt zurück: { item_id: product_type, ... }
    """
    global _vision_analyzer

    if not _vision_analyzer.is_available():
        return jsonify({
            'error':   'KI-Analyse nicht verfügbar',
            'reason':  'Kein Anthropic API Key gesetzt',
            'setup':   'Einstellungen öffnen und Anthropic API Key eintragen',
        }), 402

    data    = request.json or {}
    items   = data.get('items', [])
    query   = data.get('query', '')

    if not items:
        return jsonify({'error': 'Keine Items übergeben'}), 400

    # Cache aus DB laden
    cache_rows = VisionCache.query.all()
    cache      = {row.image_url: row.product_type for row in cache_rows}

    # Analyse durchführen (max 20 Items)
    results = _vision_analyzer.analyze_items(items, max_items=20, cache=cache)

    # Neue Ergebnisse in DB-Cache speichern
    try:
        for item in items[:20]:
            img_url     = item.get('image', '')
            item_id     = str(item.get('id', ''))
            product_type = results.get(item_id)
            if img_url and product_type and img_url not in cache:
                db.session.merge(VisionCache(
                    image_url=img_url,
                    product_type=product_type,
                ))
        db.session.commit()
    except Exception as e:
        logger.warning(f"Vision-Cache DB Fehler: {e}")
        db.session.rollback()

    return jsonify({
        'results': results,
        'count':   len(results),
    })


@api_bp.route('/settings', methods=['GET'])
def get_settings():
    s = _load_settings()
    return jsonify({
        'ebay_api_key_set':      bool(s.get('ebay_app_id')),
        'anthropic_api_key_set': bool(s.get('anthropic_api_key')),
        'vision_available':      _vision_analyzer.is_available(),
    })

@api_bp.route('/settings', methods=['POST'])
def save_settings_route():
    global _ebay_scraper, _vision_analyzer
    data = request.json or {}
    s    = _load_settings()

    if 'ebay_app_id' in data:
        s['ebay_app_id'] = data['ebay_app_id'].strip()
        _ebay_scraper = EbayScraper(app_id=s['ebay_app_id'], timeout=Config.REQUEST_TIMEOUT)
        logger.info("eBay API Key aktualisiert")

    if 'anthropic_api_key' in data:
        s['anthropic_api_key'] = data['anthropic_api_key'].strip()
        _vision_analyzer = VisionAnalyzer(api_key=s['anthropic_api_key'])
        logger.info("Anthropic API Key aktualisiert")

    _save_settings(s)
    return jsonify({'success': True})


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

@api_bp.route('/status', methods=['GET'])
def status():
    s = _load_settings()
    return jsonify({
        'status':           'ok',
        'ebay_api_key_set': bool(s.get('ebay_app_id') or Config.EBAY_APP_ID),
        'version':          '1.0.0',
    })


# ------------------------------------------------------------------
# Debug
# ------------------------------------------------------------------

@api_bp.route('/debug/search', methods=['GET'])
def debug_search():
    query = (request.args.get('q', '') or '').strip()
    if not query:
        return jsonify({'error': 'Suchbegriff fehlt'}), 400

    result = {}

    try:
        if not _vinted_scraper._ready:
            _vinted_scraper._init_session()
        from bs4 import BeautifulSoup
        import requests as _req
        vinted_url = "https://www.vinted.de/api/v2/catalog/items"
        r = _vinted_scraper.session.get(vinted_url,
            params={'search_text': query, 'per_page': 10, 'order': 'relevance'},
            timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {'raw': r.text[:300]}
        result['vinted'] = {
            'http_status':  r.status_code,
            'items_count':  len(body.get('items', [])) if isinstance(body, dict) else 0,
            'cookies':      list(_vinted_scraper.session.cookies.keys()),
            'body_sample':  str(body)[:600],
        }
        result['vinted']['parsed'] = _vinted_scraper.search(query, 5)
    except Exception as e:
        result['vinted'] = {'error': str(e)}

    try:
        ebay_url = "https://www.ebay.de/sch/i.html"
        r = _ebay_scraper.session.get(ebay_url,
            params={'_nkw': query, '_sop': '12', '_ipg': '20'}, timeout=15)
        soup    = BeautifulSoup(r.content, 'lxml')
        s_items = soup.select('.s-item')
        result['ebay'] = {
            'http_status':   r.status_code,
            'response_bytes': len(r.content),
            's_item_count':  len(s_items),
            'page_title':    soup.title.get_text() if soup.title else '',
            'parsed':        _ebay_scraper.search_sold_items(query, 5),
        }
    except Exception as e:
        result['ebay'] = {'error': str(e)}

    return jsonify(result)


# ------------------------------------------------------------------
# Intern
# ------------------------------------------------------------------

def _save_price_snapshot(brand, ebay_items, vinted_items):
    import statistics as _stats

    def _snap(source, items):
        prices = [i['price'] for i in items if i.get('price', 0) > 0]
        if not prices:
            return
        db.session.add(PriceSnapshot(
            brand      = brand,
            source     = source,
            avg_price  = round(_stats.mean(prices), 2),
            min_price  = round(min(prices), 2),
            max_price  = round(max(prices), 2),
            item_count = len(prices),
        ))

    _snap('ebay',   ebay_items)
    _snap('vinted', vinted_items)
