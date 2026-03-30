"""
eBay Scraper
============
Scraping von eBay.de verkauften Artikeln (Sold Listings).
Unterstützt optionalen API-Key für stabilere Ergebnisse.
"""

import re
import logging
import requests
from bs4 import BeautifulSoup
from typing import List, Dict

logger = logging.getLogger(__name__)

EBAY_BASE    = "https://www.ebay.de"
EBAY_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

HEADERS = {
    'User-Agent':                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                                 'Chrome/122.0.0.0 Safari/537.36',
    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language':           'de-DE,de;q=0.9,en;q=0.8',
    'Accept-Encoding':           'gzip, deflate, br',
    'DNT':                       '1',
    'Upgrade-Insecure-Requests': '1',
}


class EbayScraper:
    def __init__(self, app_id: str = '', timeout: int = 15):
        self.app_id  = app_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def search_sold_items(self, brand: str, num_items: int = 50) -> List[Dict]:
        if self.app_id:
            results = self._search_via_api(brand, num_items)
            if results:
                return results
            logger.warning("eBay API fehlgeschlagen – Fallback auf Scraping")
        return self._search_via_scraping(brand, num_items)

    # ------------------------------------------------------------------
    # API-Modus
    # ------------------------------------------------------------------
    def _search_via_api(self, brand: str, num_items: int) -> List[Dict]:
        params = {
            'OPERATION-NAME':                'findCompletedItems',
            'SERVICE-VERSION':               '1.0.0',
            'SECURITY-APPNAME':              self.app_id,
            'RESPONSE-DATA-FORMAT':          'JSON',
            'REST-PAYLOAD':                  '',
            'keywords':                      brand,
            'itemFilter(0).name':            'SoldItemsOnly',
            'itemFilter(0).value':           'true',
            'paginationInput.entriesPerPage': str(min(num_items, 100)),
            'sortOrder':                     'BestMatch',
            'outputSelector(0)':             'PictureURLLarge',
        }
        try:
            r = self.session.get(EBAY_API_URL, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            raw  = (data.get('findCompletedItemsResponse', [{}])[0]
                        .get('searchResult', [{}])[0]
                        .get('item', []))
            logger.info(f"eBay API: {len(raw)} Items für '{brand}'")
            return [i for i in [self._normalize_api(x) for x in raw] if i.get('title')]
        except Exception as e:
            logger.error(f"eBay API-Fehler: {e}")
            return []

    def _normalize_api(self, item: Dict) -> Dict:
        try:
            price = float(
                item.get('sellingStatus',  [{}])[0]
                    .get('currentPrice',   [{}])[0]
                    .get('__value__', 0)
            )
            image = (item.get('pictureURLLarge', [''])[0] or
                     item.get('galleryURL',      [''])[0])
            return {
                'source':    'ebay',
                'id':        item.get('itemId',    [''])[0],
                'title':     item.get('title',     [''])[0].strip(),
                'price':     price,
                'currency':  'EUR',
                'brand':     '',
                'size':      '',
                'condition': (item.get('condition',   [{}])[0]
                                  .get('conditionDisplayName', [''])[0]),
                'likes':     0,
                'views':     0,
                'image':     image,
                'url':       item.get('viewItemURL', [''])[0],
                'sold':      True,
                'sold_date': (item.get('listingInfo', [{}])[0]
                                  .get('endTime',     [''])[0]),
            }
        except Exception as e:
            logger.debug(f"eBay API normalize error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Scraping-Modus
    # ------------------------------------------------------------------
    def _search_via_scraping(self, brand: str, num_items: int) -> List[Dict]:
        # Aktive Listings – rendern statisch (verkaufte Artikel brauchen JS-Rendering).
        # Für Reseller genauso wertvoll: zeigt aktuelle Marktpreise & Nachfrage.
        url    = f"{EBAY_BASE}/sch/i.html"
        params = {
            '_nkw': brand,
            '_sop': '12',   # Meistgebotene/Beliebteste zuerst
            '_ipg': '60',
        }
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            logger.info(f"eBay Scraping: HTTP {r.status_code} für '{brand}', {len(r.content)} Bytes")

            soup  = BeautifulSoup(r.content, 'lxml')
            items = self._parse_listings(soup)
            logger.info(f"eBay Scraping: {len(items)} Items geparst")
            return items[:num_items]

        except requests.Timeout:
            logger.error("eBay: Timeout")
            return []
        except Exception as e:
            logger.error(f"eBay Scraping-Fehler: {e}")
            return []

    def _parse_listings(self, soup: BeautifulSoup) -> List[Dict]:
        items = []

        # Verschiedene Selektoren ausprobieren (eBay ändert HTML regelmäßig)
        listings = (soup.select('.s-item__wrapper') or
                    soup.select('.s-item')           or
                    soup.select('[data-view="mi:1686|iid:1"]'))

        logger.info(f"eBay: {len(listings)} Listing-Container gefunden")

        for listing in listings:
            try:
                # Titel – mehrere Selektoren
                title_el = (listing.select_one('.s-item__title span[role="heading"]') or
                            listing.select_one('.s-item__title')                       or
                            listing.select_one('h3'))
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not title or title in ('Shop on eBay', 'Ähnliche Artikel ansehen'):
                    continue

                # Preis
                price_el  = (listing.select_one('.s-item__price') or
                             listing.select_one('.prc'))
                price_txt = price_el.get_text(strip=True) if price_el else '0'
                price     = self._parse_price(price_txt)

                # Bild
                img_el = listing.select_one('img.s-item__image-img, img.s-item__img')
                image  = ''
                if img_el:
                    image = (img_el.get('src') or img_el.get('data-src') or '')
                    # Kleine Placeholder-Bilder überspringen
                    if 's-l140' in image or 's-l64' in image:
                        image = image.replace('s-l140', 's-l300').replace('s-l64', 's-l300')

                # Link
                link_el = (listing.select_one('a.s-item__link') or
                           listing.select_one('a[href*="itm"]'))
                url = link_el.get('href', '') if link_el else ''

                # Zustand
                cond_el   = (listing.select_one('.SECONDARY_INFO') or
                             listing.select_one('.s-item__subtitle'))
                condition = cond_el.get_text(strip=True) if cond_el else ''

                # Verkaufsdatum
                date_el   = (listing.select_one('.s-item__endedDate') or
                             listing.select_one('.POSITIVE'))
                sold_date = date_el.get_text(strip=True) if date_el else ''

                # Item-ID
                item_id = ''
                if url:
                    m = re.search(r'/itm/(\d+)', url)
                    if m:
                        item_id = m.group(1)

                if not title or price == 0:
                    continue

                items.append({
                    'source':    'ebay',
                    'id':        item_id,
                    'title':     title,
                    'price':     price,
                    'currency':  'EUR',
                    'brand':     '',
                    'size':      '',
                    'condition': condition,
                    'likes':     0,
                    'views':     0,
                    'image':     image,
                    'url':       url,
                    'sold':      False,
                    'sold_date': '',
                })
            except Exception as e:
                logger.debug(f"eBay parse error: {e}")
                continue

        return items

    @staticmethod
    def _parse_price(text: str) -> float:
        cleaned = re.sub(r'[^\d,.]', '', text)
        if not cleaned:
            return 0.0
        if ',' in cleaned and '.' in cleaned:
            cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            cleaned = cleaned.replace(',', '.')
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
