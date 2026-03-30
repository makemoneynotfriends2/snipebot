"""
Vinted Scraper – Multi-Page
============================
Fetcht mehrere Seiten parallel für aussagekräftige Trendanalyse.

Standard: 10 Seiten × 96 Items = bis zu 960 Artikel pro Suche
Außerdem: 3 verschiedene Sortierungen (relevance + newest_first + price_low_to_high)
→ bis zu ~1000+ deduplizierte Artikel für echte Marktintelligenz
"""

import requests
import time
import random
import logging
import concurrent.futures
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

VINTED_BASE     = "https://www.vinted.de"
VINTED_API_BASE = "https://www.vinted.de/api/v2"

# Maximale Items pro API-Request (Vinted-Limit)
PER_PAGE = 96


class VintedScraper:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self._ready  = False

    # ── Session ──────────────────────────────────────────────────

    def _init_session(self):
        """Startseite aufrufen um Session-Cookies (inkl. CSRF) zu setzen."""
        try:
            self.session.headers.update({
                'User-Agent':      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                                   'Chrome/122.0.0.0 Safari/537.36',
                'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Connection':      'keep-alive',
            })
            r = self.session.get(VINTED_BASE, timeout=self.timeout, allow_redirects=True)
            logger.info(f"Vinted Homepage: HTTP {r.status_code}, "
                        f"Cookies: {list(self.session.cookies.keys())}")
            time.sleep(random.uniform(0.8, 1.5))

            csrf = (self.session.cookies.get('XSRF-TOKEN') or
                    self.session.cookies.get('_vinted_fr_session', '')[:20])

            self.session.headers.update({
                'Accept':       'application/json, text/plain, */*',
                'Referer':      'https://www.vinted.de/',
                'Origin':       'https://www.vinted.de',
                'X-CSRF-Token': csrf,
            })
            self._ready = True
        except Exception as e:
            logger.warning(f"Vinted Session-Init fehlgeschlagen: {e}")
            self._ready = True

    def _ensure_session(self):
        if not self._ready:
            self._init_session()

    # ── Haupt-Suchmethode ─────────────────────────────────────────

    def search(self, brand: str, max_items: int = 1000) -> List[Dict]:
        """
        Sucht nach Marke auf Vinted – lädt mehrere Seiten parallel.

        max_items: Obergrenze der zurückgegebenen Artikel (Standard 1000).
        Intern werden bis zu 10 Seiten × 96 = 960 Items geladen,
        plus zwei weitere Sortierungen für maximale Vielfalt und Datenqualität.
        """
        self._ensure_session()

        max_pages = max(1, min((max_items + PER_PAGE - 1) // PER_PAGE, 10))

        # Seiten 1-N parallel laden (mit kleiner Verzögerung pro Request)
        all_items: List[Dict] = []
        seen_ids:  set        = set()

        # Runde 1: relevance-Sortierung, Hauptmenge
        relevance_items = self._fetch_pages(brand, order='relevance', pages=max_pages)
        for item in relevance_items:
            if item.get('id') and item['id'] not in seen_ids:
                seen_ids.add(item['id'])
                all_items.append(item)

        # Runde 2: newest_first – frische Listings, mehr Vielfalt
        if len(all_items) < max_items:
            newest_items = self._fetch_pages(brand, order='newest_first', pages=3)
            for item in newest_items:
                if item.get('id') and item['id'] not in seen_ids:
                    seen_ids.add(item['id'])
                    all_items.append(item)

        # Runde 3: price_low_to_high – günstigste Angebote erfassen
        if len(all_items) < max_items:
            cheap_items = self._fetch_pages(brand, order='price_low_to_high', pages=2)
            for item in cheap_items:
                if item.get('id') and item['id'] not in seen_ids:
                    seen_ids.add(item['id'])
                    all_items.append(item)

        logger.info(f"Vinted: {len(all_items)} deduplizierte Items für '{brand}'")
        return all_items[:max_items]

    def _fetch_pages(self, brand: str, order: str, pages: int) -> List[Dict]:
        """Lädt mehrere Seiten parallel (max. 5 gleichzeitig)."""
        results: List[Dict] = []

        def fetch_page(page: int) -> List[Dict]:
            # Kurze zufällige Pause damit Vinted uns nicht blockt
            time.sleep(random.uniform(0.05, 0.25) * page)
            return self._fetch_single_page(brand, order=order, page=page)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_page, p): p for p in range(1, pages + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    items = future.result()
                    results.extend(items)
                except Exception as e:
                    logger.warning(f"Vinted Seite fehlgeschlagen: {e}")

        return results

    def _fetch_single_page(self, brand: str, order: str = 'relevance',
                           page: int = 1) -> List[Dict]:
        """Einzelne API-Seite laden."""
        params = {
            'search_text': brand,
            'per_page':    PER_PAGE,
            'order':       order,
            'page':        page,
        }

        try:
            r = self.session.get(
                f"{VINTED_API_BASE}/catalog/items",
                params=params,
                timeout=self.timeout,
            )

            if r.status_code in (401, 403):
                logger.info(f"Vinted: Session abgelaufen (Seite {page}), erneuern…")
                self._ready = False
                self._init_session()
                r = self.session.get(
                    f"{VINTED_API_BASE}/catalog/items",
                    params=params,
                    timeout=self.timeout,
                )

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    logger.warning(f"Vinted Seite {page}: kein JSON")
                    return []

                raw   = data.get('items', [])
                items = [self._normalize(i) for i in raw if i]
                items = [i for i in items if i.get('title')]
                logger.info(f"Vinted Seite {page} ({order}): {len(items)} Items")

                # Wenn weniger als 20 Items zurück → letzte Seite erreicht
                if len(raw) < 20:
                    logger.info(f"Vinted: Letzte Seite bei {page}")

                return items

            else:
                logger.warning(f"Vinted Seite {page}: HTTP {r.status_code}")
                return []

        except requests.Timeout:
            logger.warning(f"Vinted Seite {page}: Timeout")
            return []
        except Exception as e:
            logger.error(f"Vinted Seite {page} Fehler: {e}")
            return []

    # ── Normalisierung ────────────────────────────────────────────

    def _normalize(self, item: Dict) -> Dict:
        try:
            photo     = item.get('photo') or {}
            image     = (photo.get('url') or
                         photo.get('full_size_url') or
                         photo.get('thumb_url') or '')
            url       = item.get('url') or f"/items/{item.get('id', '')}"
            if not url.startswith('http'):
                url = VINTED_BASE + url

            raw_price = item.get('price', 0)
            if isinstance(raw_price, dict):
                raw_price = raw_price.get('amount', 0)

            return {
                'source':     'vinted',
                'id':         str(item.get('id', '')),
                'title':      (item.get('title') or '').strip(),
                'price':      self._to_float(raw_price),
                'currency':   item.get('currency', 'EUR'),
                'brand':      item.get('brand_title', ''),
                'size':       item.get('size_title', ''),
                'condition':  item.get('status', ''),
                'likes':      int(item.get('favourite_count') or 0),
                'views':      int(item.get('view_count') or 0),
                'image':      image,
                'url':        url,
                'sold':       False,
                'created_ts': item.get('created_at_ts', 0),
            }
        except Exception as e:
            logger.debug(f"Vinted normalize error: {e}")
            return {}

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(str(value).replace(',', '.'))
        except Exception:
            return 0.0
