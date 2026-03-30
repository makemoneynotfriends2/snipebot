"""
Scoring & Marktanalyse Engine
==============================
Kernfrage für Reseller: "Welche ARTIKEL einer Marke sind gerade gefragt?"

V1: Title-basierte Kategorisierung mit Brand-Stripping
V1.5: Optional KI-Bilderkennung (wenn product_type via Vision gesetzt)
"""

import re
import time
import statistics
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ── Produkttyp-Keywords ────────────────────────────────────────────
# Reihenfolge: spezifischer → allgemeiner
PRODUCT_TYPES = [
    ('Polo Shirt',        ['polo shirt', 'poloshirt', 'polo hemd', 'polo-shirt']),
    ('Pullover / Strick', ['pullover', 'sweater', 'strickpullover', 'cable knit',
                           'knitwear', 'strick', 'knit sweater', 'crewneck sweater',
                           'v-neck sweater', 'strickjacke', 'cardigan']),
    ('Hoodie',            ['hoodie', 'hoody', 'kapuzenpullover', 'zip hoodie',
                           'kapuzensweatshirt']),
    ('Sweatshirt',        ['sweatshirt', 'crewneck sweat']),
    ('T-Shirt',           ['t-shirt', 'tshirt', ' tee ', 't shirt', 'basic tee']),
    ('Hemd',              [' hemd ', 'oxford shirt', 'flannel shirt',
                           'button down', 'buttondown', 'button-down',
                           'dress shirt', 'casual shirt']),
    ('Jacke',             ['jacke', ' jacket', 'harrington', 'windbreaker',
                           'puffer jacket', 'blouson', 'varsity', 'bomber',
                           'denim jacket', 'quilted jacket', 'field jacket',
                           'fleece jacket', 'softshell']),
    ('Weste',             ['weste', ' vest ', 'gilet', 'puffer vest']),
    ('Mantel',            ['mantel', ' coat', 'peacoat', 'trenchcoat', 'parka',
                           'overcoat', 'wool coat', 'duffle coat']),
    ('Jeans',             ['jeans', ' denim pant', 'denim trouser']),
    ('Hose / Chinos',     ['chino', ' hose', ' pants', 'trousers', 'cargo pant',
                           'slim pant', 'khaki']),
    ('Shorts',            ['shorts', 'bermuda', 'swim short', 'board short']),
    ('Kleid / Rock',      ['kleid', ' dress', ' rock', ' skirt', 'midi', 'maxi dress']),
    ('Schuhe',            ['schuhe', 'sneaker', 'boots', 'loafer', 'moccasin',
                           'schuh', ' shoe', 'slipper', 'espadrille']),
    ('Cap / Mütze',       ['baseball cap', ' cap ', 'mütze', 'beanie', ' hat ',
                           'snapback', 'bucket hat', 'trucker cap']),
    ('Tasche',            ['tasche', ' bag', ' tote', 'rucksack', 'backpack',
                           'crossbody', 'clutch', 'handbag']),
    ('Accessoires',       ['schal', 'gürtel', ' belt', ' scarf', 'socken', ' socks',
                           'krawatte', ' tie', ' uhr', ' watch', 'armband',
                           'brieftasche', 'wallet', 'muffler']),
]

# Typische Brand-Prefixe die VOR dem eigentlichen Markennamen stehen
# (z.B. "Polo Ralph Lauren" wenn die Marke "Ralph Lauren" ist)
BRAND_PREFIXES = ['polo', 'sport', 'vintage', 'classic', 'original', 'authentic']


def clean_title(title: str, brand: str) -> str:
    """
    Entfernt den Markennamen (und seine Varianten) aus dem Titel,
    damit der restliche Titeltext zuverlässig kategorisiert werden kann.

    Beispiel:
      title = "Polo Ralph Lauren Strickjacke Gr. M"
      brand = "ralph lauren"
      → "Strickjacke Gr. M"
    """
    cleaned = title.strip()
    brand_l = brand.lower().strip()

    if not brand_l:
        return cleaned

    # Variante 1: Exakt den Brand entfernen
    cleaned = re.sub(re.escape(brand_l), '', cleaned, flags=re.IGNORECASE).strip()

    # Variante 2: Brand mit typischen Prefixen ("Polo Ralph Lauren")
    for prefix in BRAND_PREFIXES:
        pattern = rf'\b{re.escape(prefix)}\s+{re.escape(brand_l)}\b'
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()

    # Variante 3: Nur letzter Teil des Brands (z.B. "Lauren" aus "Ralph Lauren")
    parts = brand_l.split()
    if len(parts) > 1:
        for part in parts:
            if len(part) > 3:  # kurze Wörter (Ralph) nicht einzeln entfernen
                cleaned = re.sub(rf'\b{re.escape(part)}\b', '', cleaned,
                                 flags=re.IGNORECASE).strip()

    # Mehrfache Leerzeichen bereinigen
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or title  # Fallback: original wenn alles entfernt wurde


def detect_product_type(title: str, brand: str = '') -> str:
    """
    Erkennt den Produkttyp aus dem Titel.
    Wenn brand angegeben, wird er vorher entfernt.
    Falls vision_type im Item gesetzt ist, wird dieser bevorzugt.
    """
    clean  = clean_title(title, brand)
    search = f' {clean.lower()} '

    for product_type, keywords in PRODUCT_TYPES:
        if any(kw in search for kw in keywords):
            return product_type

    return 'Sonstiges'


# ── Listing-Frische Signal ────────────────────────────────────────

def enrich_freshness(items: List[Dict]) -> None:
    """
    Enriches items with freshness metrics in-place:
    - hours_old: float, hours since created_ts (None if created_ts is 0 or invalid)
    - like_velocity: likes per hour (None if hours_old is None)
    - freshness_label: categorical label based on age
    """
    current_time = time.time()

    for item in items:
        created_ts = item.get('created_ts', 0)

        # Calculate hours_old
        if created_ts and created_ts > 0:
            hours_old = (current_time - created_ts) / 3600.0
            item['hours_old'] = hours_old
        else:
            hours_old = None
            item['hours_old'] = None

        # Calculate like_velocity
        if hours_old is not None:
            likes = item.get('likes', 0)
            item['like_velocity'] = likes / max(hours_old, 0.5)
        else:
            item['like_velocity'] = None

        # Determine freshness_label
        if hours_old is None:
            item['freshness_label'] = None
        elif hours_old < 6:
            item['freshness_label'] = 'Gerade eingestellt'
        elif hours_old < 24:
            item['freshness_label'] = 'Heute'
        elif hours_old < 168:  # 7 days
            item['freshness_label'] = 'Diese Woche'
        else:
            item['freshness_label'] = 'Älter'


def find_trending_now(items: List[Dict], max_items: int = 30) -> List[Dict]:
    """
    Finds trending items based on like_velocity.
    Filters for:
    - Vinted only
    - likes >= 1
    - hours_old is not None
    - hours_old < 72 (3 days)

    Returns top max_items sorted by like_velocity descending.
    """
    filtered = [
        i for i in items
        if (i.get('source') == 'vinted' and
            i.get('likes', 0) >= 1 and
            i.get('hours_old') is not None and
            i.get('hours_old', float('inf')) < 72)
    ]

    sorted_items = sorted(
        filtered,
        key=lambda x: x.get('like_velocity', 0),
        reverse=True
    )

    return sorted_items[:max_items]


# ── Haupt-Analyse ──────────────────────────────────────────────────

def analyze(items: List[Dict], query: str = '') -> Dict:
    """
    Vollständige Marktanalyse für Reseller.
    query: Suchbegriff (Markenname) – wird zum Brand-Stripping genutzt.
    """
    vinted = [i for i in items if i.get('source') == 'vinted' and i.get('title')]
    ebay   = [i for i in items if i.get('source') == 'ebay'   and i.get('title')]

    # Produkttyp zu jedem Item hinzufügen
    # Wenn KI-Vision bereits gesetzt (item['vision_type']), diesen bevorzugen
    for item in vinted + ebay:
        if item.get('vision_type'):
            item['product_type'] = item['vision_type']
        else:
            item['product_type'] = detect_product_type(
                item.get('title', ''), brand=query
            )

    # Enrich all items with freshness metrics
    enrich_freshness(vinted + ebay)

    product_groups = _build_product_groups(vinted, ebay)
    hot_items      = _find_hot_items(vinted, ebay)
    market_summary = _market_summary(vinted, ebay, product_groups)
    trending_now   = find_trending_now(vinted)

    return {
        'product_groups': product_groups,
        'hot_items':      hot_items,
        'market_summary': market_summary,
        'trending_now':   trending_now,
    }


def _build_price_intel(vinted_items: List[Dict], vinted_prices: List[float]) -> Dict:
    """
    Builds price intelligence for a product group.

    Returns dict with:
    - buckets: list of price buckets with label, count, avg_likes
    - sweet_spot: bucket label with highest avg_likes (if >= 3 items), else None
    - sweet_spot_range: [min_price, max_price] of sweet spot, or None
    - median_price: median of all prices, or None
    """
    price_intel = {
        'buckets': [],
        'sweet_spot': None,
        'sweet_spot_range': None,
        'median_price': None,
    }

    if not vinted_prices:
        return price_intel

    # Calculate median
    price_intel['median_price'] = _r(statistics.median(vinted_prices))

    # Handle case where all prices are the same
    if len(set(vinted_prices)) == 1:
        single_price = vinted_prices[0]
        price_intel['buckets'] = [{
            'label': f'€{_r(single_price)}',
            'count': len(vinted_prices),
            'avg_likes': _r(statistics.mean([i.get('likes', 0) for i in vinted_items])),
        }]
        return price_intel

    # Create ~5 equal-width buckets
    min_price = min(vinted_prices)
    max_price = max(vinted_prices)
    price_range = max_price - min_price

    if price_range == 0:
        bucket_width = 1
        num_buckets = 1
    else:
        num_buckets = 5
        bucket_width = price_range / num_buckets

    buckets = []
    for i in range(num_buckets):
        bucket_min = min_price + (i * bucket_width)
        bucket_max = bucket_min + bucket_width
        # Last bucket includes the max value
        if i == num_buckets - 1:
            bucket_max = max_price + 0.01

        # Find items in this bucket
        items_in_bucket = [
            item for item in vinted_items
            if (item.get('price', 0) > 0 and
                bucket_min <= item['price'] < bucket_max + 0.01)
        ]

        count = len(items_in_bucket)
        if count > 0:
            avg_likes = _r(statistics.mean([item.get('likes', 0) for item in items_in_bucket]))
        else:
            avg_likes = 0

        label = f'€{_r(bucket_min)}-{_r(bucket_max)}'
        buckets.append({
            'label': label,
            'count': count,
            'avg_likes': avg_likes,
            'min_price': _r(bucket_min),
            'max_price': _r(bucket_max),
        })

    price_intel['buckets'] = buckets

    # Find sweet spot: bucket with highest avg_likes and count >= 3
    qualified_buckets = [b for b in buckets if b['count'] >= 3]
    if qualified_buckets:
        sweet_spot_bucket = max(qualified_buckets, key=lambda b: b['avg_likes'])
        price_intel['sweet_spot'] = sweet_spot_bucket['label']
        price_intel['sweet_spot_range'] = [
            sweet_spot_bucket['min_price'],
            sweet_spot_bucket['max_price']
        ]

    return price_intel


def _build_product_groups(vinted: List[Dict], ebay: List[Dict]) -> List[Dict]:
    groups: Dict[str, Dict] = {}

    for item in vinted:
        pt = item.get('product_type', 'Sonstiges')
        g  = groups.setdefault(pt, {'product_type': pt, 'vinted_items': [], 'ebay_items': []})
        g['vinted_items'].append(item)

    for item in ebay:
        pt = item.get('product_type', 'Sonstiges')
        g  = groups.setdefault(pt, {'product_type': pt, 'vinted_items': [], 'ebay_items': []})
        g['ebay_items'].append(item)

    result = []
    for g in groups.values():
        vi, ei = g['vinted_items'], g['ebay_items']
        if not vi and not ei:
            continue

        total_likes   = sum(i.get('likes', 0) for i in vi)
        avg_likes     = round(total_likes / len(vi), 1) if vi else 0
        vinted_prices = [i['price'] for i in vi if i.get('price', 0) > 0]
        ebay_prices   = [i['price'] for i in ei if i.get('price', 0) > 0]

        demand_score = (total_likes * 2) + (len(vi) * 3) + (len(ei) * 5)

        arb = None
        if vinted_prices and ebay_prices:
            v_avg = statistics.mean(vinted_prices)
            e_avg = statistics.mean(ebay_prices)
            gap   = e_avg - v_avg
            if gap > 0 and v_avg > 0:
                arb = {
                    'gap':        _r(gap),
                    'percentage': _r((gap / v_avg) * 100, 1),
                    'buy_at':     _r(v_avg),
                    'sell_at':    _r(e_avg),
                }

        # Build price intelligence
        price_intel = _build_price_intel(vi, vinted_prices)

        # Alle Items sortiert nach Likes (für Gruppen-Drilldown im Frontend)
        all_group_items = sorted(vi + ei, key=lambda x: x.get('likes', 0), reverse=True)
        # Score/Label für alle Items setzen falls noch nicht gesetzt
        max_likes_group = all_group_items[0].get('likes', 1) if all_group_items else 1
        for it in all_group_items:
            if not it.get('score_label'):
                if it.get('source') == 'ebay':
                    it['score']       = 50.0
                    it['score_label'] = 'Aktiv'
                else:
                    lk = it.get('likes', 0)
                    sc = round((lk / max(max_likes_group, 1)) * 100, 1)
                    it['score']       = sc
                    it['score_label'] = _score_label(sc)

        # ── Reseller Intelligence Metrics ─────────────────────────────────

        # 1. Demand/Supply Ratio (DSR): avg likes per listing
        #    This is the KEY metric: low supply + high likes = market gap = opportunity
        dsr = round(total_likes / len(vi), 1) if vi else 0

        # 2. Competition level based on listing count
        if len(vi) < 8:
            competition_level = 'gering'
        elif len(vi) <= 40:
            competition_level = 'mittel'
        else:
            competition_level = 'hoch'

        # 3. Recommended sell price: from sweet spot (if exists) or median
        rec_sell = None
        if price_intel.get('sweet_spot_range'):
            rec_sell = _r(statistics.mean(price_intel['sweet_spot_range']))
        elif price_intel.get('median_price'):
            rec_sell = price_intel['median_price']
        elif vinted_prices:
            rec_sell = _r(statistics.mean(vinted_prices))

        # 4. Max buy price: what to pay at flea market/thrift store MAX
        #    Formula: rec_sell × 0.38 (leaves room for Vinted fees + ~50% margin)
        rec_buy_max = _r(rec_sell * 0.38) if rec_sell else None

        # 5. Estimated net margin after Vinted fee (~5% + €0.70)
        vinted_fee = _r(rec_sell * 0.05 + 0.70) if rec_sell else None
        expected_margin = _r(rec_sell - (rec_buy_max or 0) - (vinted_fee or 0)) if rec_sell and rec_buy_max else None
        expected_margin_pct = round((expected_margin / rec_sell) * 100) if rec_sell and expected_margin else None

        # 6. Buy signal: the core reseller recommendation
        #    Rules:
        #    - 'unklar': fewer than 5 listings (not enough data)
        #    - 'kaufen': DSR >= 10 AND not high competition (strong market gap)
        #    - 'prüfen': DSR >= 5 OR (DSR >= 3 AND competition gering/mittel)
        #    - 'meiden': DSR < 3 OR (high competition AND low DSR)
        if len(vi) < 5:
            buy_signal = 'unklar'
        elif dsr >= 10 and competition_level in ('gering', 'mittel'):
            buy_signal = 'kaufen'
        elif dsr >= 5 or (dsr >= 3 and competition_level == 'gering'):
            buy_signal = 'prüfen'
        else:
            buy_signal = 'meiden'

        result.append({
            'product_type':      g['product_type'],
            'demand_score':      demand_score,
            'vinted_count':      len(vi),
            'ebay_count':        len(ei),
            'total_likes':       total_likes,
            'avg_likes':         avg_likes,
            'vinted_avg_price':  _r(statistics.mean(vinted_prices)) if vinted_prices else 0,
            'vinted_min_price':  _r(min(vinted_prices)) if vinted_prices else 0,
            'vinted_max_price':  _r(max(vinted_prices)) if vinted_prices else 0,
            'ebay_avg_price':    _r(statistics.mean(ebay_prices)) if ebay_prices else 0,
            'arbitrage':         arb,
            'price_intel':       price_intel,
            'demand_label':      _demand_label(demand_score, total_likes),
            'top_items':         sorted(vi, key=lambda x: x.get('likes', 0), reverse=True)[:3],
            'items':             all_group_items,
            'dsr':               dsr,
            'competition_level': competition_level,
            'recommended_sell':  rec_sell,
            'recommended_buy_max': rec_buy_max,
            'expected_margin':   expected_margin,
            'expected_margin_pct': expected_margin_pct,
            'buy_signal':        buy_signal,
        })

    result.sort(key=lambda x: (x['product_type'] == 'Sonstiges', -x['demand_score']))

    if result:
        max_score = max((r['demand_score'] for r in result if r['product_type'] != 'Sonstiges'), default=1)
        for r in result:
            r['demand_pct'] = min(round((r['demand_score'] / max(max_score, 1)) * 100), 100)

    return result


def _find_hot_items(vinted: List[Dict], ebay: List[Dict]) -> List[Dict]:
    v_sorted = sorted(vinted, key=lambda x: x.get('likes', 0), reverse=True)
    e_sorted = sorted(ebay,   key=lambda x: x.get('price', 0), reverse=True)

    max_likes = v_sorted[0].get('likes', 1) if v_sorted else 1
    for item in v_sorted:
        likes = item.get('likes', 0)
        item['score']       = round((likes / max(max_likes, 1)) * 100, 1)
        item['score_label'] = _score_label(item['score'])

    for item in e_sorted:
        item['score']       = 50.0
        item['score_label'] = 'Aktiv'

    combined = v_sorted[:15] + e_sorted[:5]
    combined.sort(key=lambda x: x.get('score', 0), reverse=True)
    return combined[:20]


def _market_summary(vinted, ebay, groups):
    s = {
        'total_vinted':     len(vinted),
        'total_ebay':       len(ebay),
        'total_likes':      sum(i.get('likes', 0) for i in vinted),
        'top_product':      groups[0]['product_type'] if groups else None,
        'top_demand_label': groups[0].get('demand_label') if groups else None,
    }
    vp = [i['price'] for i in vinted if i.get('price', 0) > 0]
    ep = [i['price'] for i in ebay   if i.get('price', 0) > 0]
    if vp: s['vinted_avg_price'] = _r(statistics.mean(vp))
    if ep: s['ebay_avg_price']   = _r(statistics.mean(ep))

    arb_groups = [g for g in groups if g.get('arbitrage')]
    if arb_groups:
        best = max(arb_groups, key=lambda x: x['arbitrage']['percentage'])
        s['best_arbitrage'] = {'product_type': best['product_type'], **best['arbitrage']}

    # Market intelligence summary
    all_dsrs = [g['dsr'] for g in groups if g['product_type'] != 'Sonstiges' and g.get('dsr', 0) > 0]
    avg_dsr = round(statistics.mean(all_dsrs), 1) if all_dsrs else 0

    buy_opportunities = [g['product_type'] for g in groups
                         if g.get('buy_signal') == 'kaufen']

    # Market health based on avg DSR
    if avg_dsr >= 8:
        market_health = 'aktiv'      # strong buyer demand
    elif avg_dsr >= 4:
        market_health = 'mittel'
    else:
        market_health = 'träge'      # oversaturated or low demand

    s['avg_dsr'] = avg_dsr
    s['buy_opportunities'] = buy_opportunities
    s['market_health'] = market_health

    return s


def _demand_label(score, likes):
    if score >= 200 or likes >= 100: return '🔥 Sehr gefragt'
    elif score >= 80 or likes >= 30: return '⚡ Gefragt'
    elif score >= 30 or likes >= 10: return '◎ Moderat'
    return '↓ Gering'


def _score_label(score):
    if score >= 70: return 'Heiß'
    elif score >= 40: return 'Gut'
    elif score >= 20: return 'Ok'
    return 'Schwach'


def _r(v, d=2):
    try: return round(float(v), d)
    except: return 0.0


# Legacy
def score_items(items):
    vinted = [i for i in items if i.get('source') == 'vinted' and i.get('title')]
    ebay   = [i for i in items if i.get('source') == 'ebay'   and i.get('title')]
    return _find_hot_items(vinted, ebay)

def get_insights(items):
    return {}
