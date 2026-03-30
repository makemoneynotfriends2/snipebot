"""
Vinted Live Poller  –  V3 (Pro-Profil-Suche + Rejected-Buffer)
===============================================================
Verbesserungen gegenüber V2:
  • Pro-Profil gezielter Suchtext: "adidas marseille tracksuit" statt nur "adidas"
    → Vinted's eigene Suchmaschine filtert fuzzy & mehrsprachig vor
  • Entspannter Lokalfilter: Keywords sind OPTIONAL wenn Marke klar matcht
    → "Adidas OM Jacke" oder "Survêtement Adidas" werden nicht mehr fälschlich
       rausgeworfen weil "trainingsanzug" nicht im Titel steht
  • Rejected-Buffer: letzte 150 abgelehnten Artikel mit Grund (für Admin-Ansicht)
  • KI-Vision: läuft nur wenn ANTHROPIC_API_KEY gesetzt ist
  • Discord-Alerts weiterhin bei jedem Match
"""

import os
import re
import time
import logging
import threading
from queue import Queue
from typing import Dict, List, Optional
import requests
import random

logger = logging.getLogger(__name__)

VINTED_BASE     = "https://www.vinted.de"
VINTED_API_BASE = "https://www.vinted.de/api/v2"
PER_PAGE        = 96
MAX_REJECTED    = 150   # Wie viele abgelehnte Artikel im Memory halten


class VintedLivePoller:
    """
    Echtzeit-Poller für neue Vinted-Listings.

    Poll-Strategie (V3):
    Für jedes Profil wird ein eigener gezielter Suchtext generiert:
      brands[0] + längster keyword → z.B. "adidas marseille tracksuit"
    Vinted's eigene Suche filtert damit besser als reines newest_first über alle Items.
    Der lokale Filter prüft nur noch Preis und Markenfeld.
    """

    def __init__(self):
        self.session     = requests.Session()
        self.running     = False
        self._thread: Optional[threading.Thread] = None
        self.queue       = Queue(maxsize=500)
        self._seen_ids   = set()
        self.config: Dict = {}
        self.profiles: List[Dict] = []
        self._vision     = None
        self._rejected: List[Dict] = []   # Rolling buffer
        self.stats = {
            'started_at':    None,
            'polls':         0,
            'items_seen':    0,
            'items_passed':  0,
            'items_rejected': 0,
            'vision_checks': 0,
            'last_poll':     None,
            'status':        'stopped',
        }
        self._init_session()

    # ── Session ───────────────────────────────────────────────────

    def _init_session(self):
        try:
            self.session.headers.update({
                'User-Agent':      ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                                    'Chrome/122.0.0.0 Safari/537.36'),
                'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Connection':      'keep-alive',
            })
            r = self.session.get(VINTED_BASE, timeout=15, allow_redirects=True)
            logger.info(f"Vinted Session: HTTP {r.status_code}")
            time.sleep(random.uniform(0.8, 1.5))
            csrf = self.session.cookies.get('XSRF-TOKEN', '')
            self.session.headers.update({
                'Accept':       'application/json, text/plain, */*',
                'Referer':      'https://www.vinted.de/',
                'Origin':       'https://www.vinted.de',
                'X-CSRF-Token': csrf,
            })
        except Exception as e:
            logger.warning(f"Session-Init: {e}")

    # ── Vision (Lazy) ─────────────────────────────────────────────

    def _get_vision(self):
        if self._vision is None:
            from analysis.vision import VisionAnalyzer
            self._vision = VisionAnalyzer(api_key=self.config.get('anthropic_api_key', ''))
        return self._vision

    # ── Start / Stop ──────────────────────────────────────────────

    def start(self, profiles: List[Dict], merged_config: Optional[Dict] = None):
        cfg = merged_config or {}

        if self.running:
            self.profiles = profiles
            self.config.update(cfg)
            self._vision = None
            logger.info("Config live aktualisiert")
            return

        self.profiles = profiles
        self.config   = cfg
        interval      = max(min((p.get('interval') or 15 for p in profiles), default=15), 10)
        self.config['interval'] = interval

        self.running = True
        self._vision = None
        self._rejected.clear()
        self.stats.update({
            'started_at':     time.time(),
            'polls':          0,
            'items_seen':     0,
            'items_passed':   0,
            'items_rejected': 0,
            'vision_checks':  0,
            'status':         'running',
        })

        self._warm_up()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"Live-Poller V3 gestartet | {len(profiles)} Profile | Interval {interval}s")

    def stop(self):
        self.running = False
        self.stats['status'] = 'stopped'
        logger.info("Live-Poller gestoppt")

    # ── Warm-Up ───────────────────────────────────────────────────

    def _warm_up(self):
        """Aktuelle Listings pro Profil vormerken – kein Flood-Alert beim Start."""
        logger.info("Warm-up gestartet…")
        for profile in self.profiles:
            try:
                search = self._search_text_for_profile(profile)
                items  = self._fetch_page(search_text=search)
                for item in items:
                    if item.get('id'):
                        self._seen_ids.add(str(item['id']))
                time.sleep(1.5)   # Kurze Pause zwischen Profil-Warm-Ups
            except Exception as e:
                logger.warning(f"Warm-up '{profile.get('name')}': {e}")
        logger.info(f"Warm-up fertig: {len(self._seen_ids)} IDs vorgemerkt")

    # ── Poll-Loop ─────────────────────────────────────────────────

    def _poll_loop(self):
        while self.running:
            try:
                self._do_poll()
            except Exception as e:
                logger.error(f"Poll-Fehler: {e}")
                self.stats['status'] = 'error'
            interval = self.config.get('interval', 15)
            time.sleep(max(interval, 10))

    def _do_poll(self):
        self.stats['polls']    += 1
        self.stats['last_poll'] = time.time()
        self.stats['status']    = 'running'

        for i, profile in enumerate(self.profiles):
            if not self.running:
                break
            self._poll_for_profile(profile)
            # Kurze Pause zwischen mehreren Profil-Suchen (Rate-Limit-Schutz)
            if i < len(self.profiles) - 1:
                time.sleep(random.uniform(1.5, 3.0))

    def _poll_for_profile(self, profile: Dict):
        search_text = self._search_text_for_profile(profile)
        items       = self._fetch_page(search_text=search_text)

        new_items = []
        for item in items:
            item_id = str(item.get('id', ''))
            if not item_id or item_id in self._seen_ids:
                continue
            self._seen_ids.add(item_id)
            self.stats['items_seen'] += 1
            normalized = self._normalize(item)
            if normalized.get('title'):
                new_items.append(normalized)

        if new_items:
            logger.info(f"[{profile['name']}] {len(new_items)} neue Items")

        for item in new_items:
            passed, reason = self._passes_filter(item, profile)
            if passed:
                self._process_match(item, profile)
            else:
                self._add_rejected(item, profile, reason)

    # ── Suchtext pro Profil ───────────────────────────────────────

    def _search_text_for_profile(self, profile: Dict) -> str:
        """
        Generiert einen gezielten Suchtext für die Vinted-API.

        Strategie: Erste (wichtigste) Marke + spezifischstes Keyword.
        Beispiel: brands=['adidas','marseille'], keywords=['trainingsanzug','marseille tracksuit']
          → "adidas marseille tracksuit"

        Dadurch filtert Vinted's eigene Suchmaschine bereits fuzzy & mehrsprachig vor.
        """
        brands   = [b.strip() for b in (profile.get('brands')   or []) if b.strip()]
        keywords = [k.strip() for k in (profile.get('keywords') or []) if k.strip()]

        # Hauptmarke: erste (bewusst gewählte Reihenfolge durch User)
        brand = brands[0] if brands else ''

        # Bestes Keyword: längstes ist am spezifischsten
        keyword = max(keywords, key=len, default='') if keywords else ''

        if brand and keyword:
            return f"{brand} {keyword}"
        return brand or keyword or ''

    # ── Filter ────────────────────────────────────────────────────

    def _passes_filter(self, item: Dict, profile: Dict):
        """
        Gibt (passed: bool, reason: str) zurück.

        Strategie: Vinted hat den Suchtext bereits gefiltert → Keywords sind
        im Lokalfilter OPTIONAL wenn die Marke klar matcht.
        Nur noch Preis + Marke lokal prüfen.
        """
        title     = (item.get('title') or '').lower()
        price     = item.get('price', 0)
        brands    = [b.lower().strip() for b in (profile.get('brands')   or []) if b.strip()]
        keywords  = [k.lower().strip() for k in (profile.get('keywords') or []) if k.strip()]
        price_min = profile.get('price_from')
        price_max = profile.get('price_to')

        # ── Preis ──────────────────────────────────────────────
        if price_min and price < price_min:
            return False, f"Preis {price:.0f}€ unter Min {price_min:.0f}€"
        if price_max and price > price_max:
            return False, f"Preis {price:.0f}€ über Max {price_max:.0f}€"

        # ── Marke ──────────────────────────────────────────────
        if brands:
            matched = self._brand_matches(item, brands, title)
            if not matched:
                item_brand = (item.get('brand') or '').strip() or '(kein Feld)'
                return False, f"Marke '{item_brand}' passt nicht zu {brands[:3]}"

        # ── Keywords: nur als Fallback wenn kein Markenfeld ────
        has_brand_field = bool((item.get('brand') or '').strip())
        if keywords and not has_brand_field:
            if not any(kw in title for kw in keywords):
                return False, f"Kein Keyword im Titel (kein Markenfeld)"

        return True, 'ok'

    # ── Marken-Matching ───────────────────────────────────────────

    @staticmethod
    def _brand_matches(item: Dict, filter_brands: List[str], title: str) -> bool:
        """
        Starts-With-Matching gegen das Markenfeld.
        'marseille' matcht 'Marseille FC' aber NICHT 'Banditas from Marseille'.
        Fallback auf Ganz-Wort-Suche im Titel wenn Markenfeld leer.
        """
        item_brand = (item.get('brand') or '').lower().strip()

        if item_brand:
            for fb in filter_brands:
                if item_brand == fb:
                    return True
                if item_brand.startswith(fb + ' '):
                    return True
                if fb.startswith(item_brand + ' ') and len(item_brand) >= 4:
                    return True
            return False
        else:
            for fb in filter_brands:
                pattern = r'(?<![a-zäöü])' + re.escape(fb) + r'(?![a-zäöü])'
                if re.search(pattern, title):
                    return True
            return False

    # ── Match verarbeiten ─────────────────────────────────────────

    def _process_match(self, item: Dict, profile: Dict):
        image_paths   = profile.get('image_paths') or []
        vision_matched = False

        if image_paths and item.get('image'):
            vision = self._get_vision()
            if vision.is_available():
                self.stats['vision_checks'] += 1
                logger.info(f"Vision-Check: '{item['title'][:40]}'")
                matched = vision.match_listing_to_profile(item['image'], image_paths)
                if not matched:
                    self._add_rejected(item, profile, 'KI: kein Bild-Match')
                    return
                vision_matched = True

        self.stats['items_passed'] += 1
        item['matched_profile']    = profile['name']
        item['matched_profile_id'] = profile.get('id')
        item['vision_matched']     = vision_matched

        try:
            self.queue.put_nowait({
                'type':           'item',
                'item':           item,
                'profile_name':   profile['name'],
                'vision_matched': vision_matched,
            })
        except Exception:
            pass

        self._send_discord(item, profile['name'], vision_matched, profile=profile)

    # ── Rejected-Buffer ───────────────────────────────────────────

    def _add_rejected(self, item: Dict, profile: Dict, reason: str):
        self.stats['items_rejected'] += 1
        entry = {
            'id':           item.get('id', ''),
            'title':        item.get('title', ''),
            'brand':        item.get('brand', ''),
            'price':        item.get('price', 0),
            'image':        item.get('image', ''),
            'url':          item.get('url', ''),
            'profile':      profile.get('name', ''),
            'reason':       reason,
            'rejected_at':  time.time(),
        }
        self._rejected.insert(0, entry)
        if len(self._rejected) > MAX_REJECTED:
            self._rejected.pop()

    def get_rejected(self, limit: int = 50) -> List[Dict]:
        return self._rejected[:limit]

    # ── Vinted API ────────────────────────────────────────────────

    def _fetch_page(self, search_text: str = '') -> List[Dict]:
        params: Dict = {
            'order':    'newest_first',
            'per_page': PER_PAGE,
        }
        if search_text:
            params['search_text'] = search_text

        try:
            r = self.session.get(
                f"{VINTED_API_BASE}/catalog/items",
                params=params,
                timeout=15,
            )
            if r.status_code in (401, 403):
                logger.info("Session erneuern…")
                self._init_session()
                r = self.session.get(
                    f"{VINTED_API_BASE}/catalog/items",
                    params=params,
                    timeout=15,
                )
            if r.status_code == 429:
                logger.warning("Rate-Limit! Pause 60s…")
                time.sleep(60)
                self.stats['status'] = 'rate_limited'
                return []
            if r.status_code == 200:
                return r.json().get('items', [])
            logger.warning(f"Vinted: HTTP {r.status_code}")
            return []
        except Exception as e:
            logger.error(f"Fetch-Fehler: {e}")
            return []

    # ── Normalisierung ────────────────────────────────────────────

    def _normalize(self, raw: Dict) -> Dict:
        try:
            photo     = raw.get('photo') or {}
            image     = (photo.get('url') or photo.get('full_size_url') or
                         photo.get('thumb_url') or '')
            url       = raw.get('url') or f"/items/{raw.get('id', '')}"
            if not url.startswith('http'):
                url = VINTED_BASE + url

            raw_price = raw.get('price', 0)
            if isinstance(raw_price, dict):
                raw_price = raw_price.get('amount', 0)

            return {
                'source':             'vinted',
                'id':                 str(raw.get('id', '')),
                'title':              (raw.get('title') or '').strip(),
                'price':              float(str(raw_price).replace(',', '.') or 0),
                'currency':           raw.get('currency', 'EUR'),
                'brand':              raw.get('brand_title', ''),
                'size':               raw.get('size_title', ''),
                'condition':          raw.get('status', ''),
                'likes':              int(raw.get('favourite_count') or 0),
                'image':              image,
                'url':                url,
                'created_ts':         raw.get('created_at_ts', 0),
                'detected_at':        time.time(),
                'matched_profile':    '',
                'matched_profile_id': None,
                'vision_matched':     False,
            }
        except Exception as e:
            logger.debug(f"Normalize-Fehler: {e}")
            return {}

    # ── Discord ───────────────────────────────────────────────────

    def _send_discord(self, item: Dict, profile_name: str, vision_matched: bool, profile: Dict = None):
        # Profil-spezifischer Webhook hat Vorrang, sonst globaler
        webhook_url = (profile or {}).get('discord_webhook', '').strip() \
                      or self.config.get('discord_webhook', '').strip()
        if not webhook_url:
            return
        try:
            color  = 0x30D158 if vision_matched else 0x0A84FF
            label  = '✅ KI-Match' if vision_matched else '🔍 Filter-Match'
            price  = f"€{item['price']:.2f}".replace('.', ',') if item.get('price') else '–'
            payload = {
                'embeds': [{
                    'title':     f"🎯 {item['title'][:100]}",
                    'url':       item.get('url', ''),
                    'color':     color,
                    'fields':    [
                        {'name': 'Preis',    'value': price,                        'inline': True},
                        {'name': 'Marke',    'value': item.get('brand') or '–',     'inline': True},
                        {'name': 'Größe',    'value': item.get('size')  or '–',     'inline': True},
                        {'name': 'Profil',   'value': profile_name or '–',          'inline': True},
                        {'name': 'KI-Check', 'value': label,                        'inline': True},
                        {'name': 'Zustand',  'value': item.get('condition') or '–', 'inline': True},
                    ],
                    'thumbnail': {'url': item.get('image', '')},
                    'footer':    {'text': '⚡ Snipe Bot • Vinted'},
                    'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
                }]
            }
            resp = requests.post(webhook_url, json=payload, timeout=6)
            if resp.status_code not in (200, 204):
                logger.warning(f"Discord HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Discord fehlgeschlagen: {e}")

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        s = dict(self.stats)
        s['profile_count'] = len(self.profiles)
        s['queue_len']     = self.queue.qsize()
        s['seen_ids']      = len(self._seen_ids)
        s['vision_active'] = bool(self.config.get('anthropic_api_key'))
        s['rejected_count'] = len(self._rejected)
        if s.get('started_at'):
            s['uptime_sec'] = round(time.time() - s['started_at'])
        return s
