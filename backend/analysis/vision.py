"""
KI-Bilderkennung für Kleidungsstücke
======================================
Zwei Funktionen:
  1. analyze_image_url()       → Produkttyp-Klassifizierung (Hoodie, T-Shirt …)
  2. match_listing_to_profile() → Listing gegen Trainingsbilder eines Profils abgleichen

Kosten: ~0,001 € pro Bild (sehr günstig)
Voraussetzung: ANTHROPIC_API_KEY in config.py oder ENV gesetzt
"""

import os
import logging
import base64
from typing import List, Dict, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger(__name__)

VALID_TYPES = [
    'Polo Shirt', 'Pullover / Strick', 'Hoodie', 'Sweatshirt', 'T-Shirt',
    'Hemd', 'Jacke', 'Weste', 'Mantel', 'Jeans', 'Hose / Chinos', 'Shorts',
    'Kleid / Rock', 'Schuhe', 'Cap / Mütze', 'Tasche', 'Accessoires', 'Sonstiges'
]

VISION_PROMPT = f"""Du analysierst ein Kleidungsstück für ein Reseller-Tool.
Bestimme den genauen Typ aus dieser Liste:

{chr(10).join(f'- {t}' for t in VALID_TYPES)}

Wichtig:
- Polo Shirt = Shirt MIT Polokragen (2-3 Knöpfe am Hals), KEIN Pullover
- Pullover / Strick = gestrickter Pullover OHNE Kapuze
- Hoodie = Kapuzenpullover oder Kapuzenjacke MIT Kapuze
- Jacke = Oberbekleidung die vorne komplett aufgeht

Antworte NUR mit dem exakten Typnamen aus der Liste, ohne weitere Erklärung."""

MATCH_PROMPT = (
    "Du bist ein Reseller-Assistent. Ich zeige dir Referenzbilder und ein neues Vinted-Listing.\n\n"
    "Beurteile: Zeigt das neue Listing das GLEICHE MODELL, das gleiche Design oder einen "
    "sehr ähnlichen Artikel (gleiche Kollektion, gleiches Muster, gleicher Stil) wie "
    "eines der Referenzbilder?\n\n"
    "Achte besonders auf:\n"
    "- Gleiche Grafiken / Prints / Logos\n"
    "- Gleiches Farbschema\n"
    "- Gleiche Kollektion / Serie\n\n"
    "Antworte NUR mit 'JA' oder 'NEIN'."
)


class VisionAnalyzer:
    def __init__(self, api_key: str = ''):
        self.api_key = api_key
        self._client = None

    def _get_client(self):
        if not self._client:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise RuntimeError(
                    "anthropic Paket nicht installiert. "
                    "Führe aus: pip install anthropic --break-system-packages"
                )
        return self._client

    def is_available(self) -> bool:
        """Gibt True zurück wenn API Key gesetzt und anthropic installiert."""
        if not self.api_key:
            return False
        try:
            import anthropic
            return True
        except ImportError:
            return False

    # ── Produkttyp-Klassifizierung ────────────────────────────────

    def analyze_image_url(self, image_url: str) -> Optional[str]:
        """
        Analysiert ein Bild per URL und gibt den Produkttyp zurück.
        Gibt None zurück bei Fehler.
        """
        if not self.api_key:
            return None

        try:
            img_data, media_type = self._fetch_image(image_url)
            if not img_data:
                return None

            client = self._get_client()
            message = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=30,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type':   'image',
                            'source': {
                                'type':       'base64',
                                'media_type': media_type,
                                'data':       img_data,
                            },
                        },
                        {'type': 'text', 'text': VISION_PROMPT}
                    ],
                }]
            )

            result = message.content[0].text.strip()
            for vt in VALID_TYPES:
                if vt.lower() in result.lower() or result.lower() in vt.lower():
                    logger.debug(f"Vision: '{result}' → '{vt}'")
                    return vt

            logger.warning(f"Vision: unbekannter Typ '{result}'")
            return None

        except Exception as e:
            logger.error(f"Vision-Fehler für {image_url[:60]}: {e}")
            return None

    def analyze_items(
        self,
        items: List[Dict],
        max_items: int = 20,
        cache: Optional[dict] = None,
    ) -> Dict[str, str]:
        """
        Analysiert mehrere Items und gibt {item_id: product_type} zurück.
        """
        if not self.is_available():
            return {}

        results = {}
        cache   = cache or {}
        count   = 0

        for item in items[:max_items]:
            img_url  = item.get('image', '')
            item_id  = str(item.get('id', ''))

            if not img_url or not item_id:
                continue

            if img_url in cache:
                results[item_id] = cache[img_url]
                continue

            product_type = self.analyze_image_url(img_url)
            if product_type:
                results[item_id]  = product_type
                cache[img_url]    = product_type
                count += 1

        logger.info(f"Vision: {count} neue Analysen, {len(results)} total")
        return results

    # ── Profil-Matching ───────────────────────────────────────────

    def match_listing_to_profile(
        self,
        listing_image_url: str,
        training_image_paths: List[str],
        max_training_images: int = 5,
    ) -> bool:
        """
        Vergleicht das Listing-Bild gegen die Trainingsbilder eines Watch-Profils.

        Returns:
            True  → Listing ist ein Match (oder kein API Key / keine Trainingsbilder → immer True)
            False → Kein Match laut KI
        """
        if not self.api_key:
            logger.debug("Vision-Match: kein API Key → durchgelassen")
            return True

        if not training_image_paths:
            logger.debug("Vision-Match: keine Trainingsbilder → durchgelassen")
            return True

        # Listing-Bild laden
        listing_data, listing_type = self._fetch_image(listing_image_url)
        if not listing_data:
            logger.debug("Vision-Match: Listing-Bild nicht ladbar → durchgelassen")
            return True

        # Trainingsbilder laden (max N, lokale Dateien)
        training_images: List[Tuple[str, str]] = []
        for path in training_image_paths[:max_training_images]:
            img_data, media_type = self._load_local_image(path)
            if img_data:
                training_images.append((img_data, media_type))

        if not training_images:
            logger.debug("Vision-Match: keine Trainingsbilder geladen → durchgelassen")
            return True

        try:
            client  = self._get_client()
            content = []

            # Referenzbilder senden
            for i, (img_data, media_type) in enumerate(training_images):
                content.append({'type': 'text', 'text': f'Referenzbild {i + 1}:'})
                content.append({
                    'type':   'image',
                    'source': {'type': 'base64', 'media_type': media_type, 'data': img_data},
                })

            # Listing-Bild senden
            content.append({'type': 'text', 'text': 'Neues Vinted-Listing:'})
            content.append({
                'type':   'image',
                'source': {'type': 'base64', 'media_type': listing_type, 'data': listing_data},
            })

            content.append({'type': 'text', 'text': MATCH_PROMPT})

            message = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=5,
                messages=[{'role': 'user', 'content': content}]
            )

            result = message.content[0].text.strip().upper()
            matched = 'JA' in result or 'YES' in result
            logger.info(f"Vision-Match: {'✓ JA' if matched else '✗ NEIN'} (raw='{result}')")
            return matched

        except Exception as e:
            logger.error(f"Vision-Match-Fehler: {e}")
            return True   # Bei API-Fehler immer durchlassen

    # ── Hilfsmethoden ─────────────────────────────────────────────

    @staticmethod
    def _fetch_image(url: str) -> Tuple[Optional[str], str]:
        """Lädt Bild per URL und gibt (base64_data, media_type) zurück."""
        try:
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; MarktanalyseTool/1.0)'
            })
            with urlopen(req, timeout=8) as resp:
                content_type = resp.headers.get('Content-Type', 'image/jpeg')
                media_type   = content_type.split(';')[0].strip()
                if media_type not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
                    media_type = 'image/jpeg'
                raw_data = resp.read()
                if len(raw_data) > 2_000_000:   # Max 2 MB
                    return None, ''
                return base64.standard_b64encode(raw_data).decode('utf-8'), media_type
        except Exception as e:
            logger.debug(f"Image fetch error ({url[:60]}): {e}")
            return None, ''

    @staticmethod
    def _load_local_image(path: str) -> Tuple[Optional[str], str]:
        """Lädt ein lokales Bild und gibt (base64_data, media_type) zurück."""
        try:
            ext_map = {
                '.jpg':  'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png':  'image/png',
                '.webp': 'image/webp',
                '.gif':  'image/gif',
            }
            ext        = os.path.splitext(path)[1].lower()
            media_type = ext_map.get(ext, 'image/jpeg')
            with open(path, 'rb') as f:
                raw_data = f.read()
            if len(raw_data) > 2_000_000:
                return None, ''
            return base64.standard_b64encode(raw_data).decode('utf-8'), media_type
        except Exception as e:
            logger.debug(f"Local image load error ({path}): {e}")
            return None, ''
