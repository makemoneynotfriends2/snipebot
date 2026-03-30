from datetime import datetime
from .db import db


class SearchHistory(db.Model):
    """Speichert alle bisherigen Suchanfragen"""
    __tablename__ = 'search_history'

    id            = db.Column(db.Integer, primary_key=True)
    query         = db.Column(db.String(255), nullable=False)
    timestamp     = db.Column(db.DateTime, default=datetime.utcnow)
    result_count  = db.Column(db.Integer, default=0)
    ebay_count    = db.Column(db.Integer, default=0)
    vinted_count  = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id':           self.id,
            'query':        self.query,
            'timestamp':    self.timestamp.isoformat(),
            'result_count': self.result_count,
            'ebay_count':   self.ebay_count,
            'vinted_count': self.vinted_count,
        }


class SavedItem(db.Model):
    """Vom User manuell gespeicherte Artikel (Watchlist)"""
    __tablename__ = 'saved_items'

    id          = db.Column(db.Integer, primary_key=True)
    source      = db.Column(db.String(50))          # 'ebay' | 'vinted'
    external_id = db.Column(db.String(255))
    title       = db.Column(db.String(500))
    price       = db.Column(db.Float)
    currency    = db.Column(db.String(10), default='EUR')
    brand       = db.Column(db.String(255))
    score       = db.Column(db.Float)
    score_label = db.Column(db.String(50))
    url         = db.Column(db.String(1000))
    image       = db.Column(db.String(1000))
    condition   = db.Column(db.String(255))
    size        = db.Column(db.String(100))
    likes       = db.Column(db.Integer, default=0)
    saved_at    = db.Column(db.DateTime, default=datetime.utcnow)
    notes       = db.Column(db.Text, default='')

    def to_dict(self):
        return {
            'id':          self.id,
            'source':      self.source,
            'external_id': self.external_id,
            'title':       self.title,
            'price':       self.price,
            'currency':    self.currency,
            'brand':       self.brand,
            'score':       self.score,
            'score_label': self.score_label,
            'url':         self.url,
            'image':       self.image,
            'condition':   self.condition,
            'size':        self.size,
            'likes':       self.likes,
            'saved_at':    self.saved_at.isoformat(),
            'notes':       self.notes,
        }


class VisionCache(db.Model):
    """Cache für KI-Bildanalyse-Ergebnisse (spart API-Kosten)"""
    __tablename__ = 'vision_cache'

    id           = db.Column(db.Integer, primary_key=True)
    image_url    = db.Column(db.String(1000), unique=True, nullable=False)
    product_type = db.Column(db.String(100))
    analyzed_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'image_url':    self.image_url,
            'product_type': self.product_type,
        }


# V2-Erweiterung: PriceSnapshot für historische Preisverfolgung
class PriceSnapshot(db.Model):
    """Preisverläufe über Zeit – wird in V2 aktiv genutzt"""
    __tablename__ = 'price_snapshots'

    id          = db.Column(db.Integer, primary_key=True)
    brand       = db.Column(db.String(255), nullable=False)
    source      = db.Column(db.String(50))
    avg_price   = db.Column(db.Float)
    min_price   = db.Column(db.Float)
    max_price   = db.Column(db.Float)
    item_count  = db.Column(db.Integer)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'brand':       self.brand,
            'source':      self.source,
            'avg_price':   self.avg_price,
            'min_price':   self.min_price,
            'max_price':   self.max_price,
            'item_count':  self.item_count,
            'recorded_at': self.recorded_at.isoformat(),
        }


class WatchProfile(db.Model):
    """Watch Profile für automatische Verfolgung von Produkten mit benutzerdefinierten Kriterien"""
    __tablename__ = 'watch_profiles'

    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(100), nullable=False)
    brands_json      = db.Column(db.Text, default='[]')   # JSON array as string
    keywords_json    = db.Column(db.Text, default='[]')
    price_from       = db.Column(db.Float, nullable=True)
    price_to         = db.Column(db.Float, nullable=True)
    interval         = db.Column(db.Integer, default=15)
    discord_webhook  = db.Column(db.String(500), nullable=True, default='')
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        import json, os
        from config import Config
        image_dir = os.path.join(Config.TRAINING_IMAGES_DIR, str(self.id))
        images = []
        if os.path.isdir(image_dir):
            images = sorted([
                f for f in os.listdir(image_dir)
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
            ])
        return {
            'id':              self.id,
            'name':            self.name,
            'brands':          json.loads(self.brands_json or '[]'),
            'keywords':        json.loads(self.keywords_json or '[]'),
            'price_from':      self.price_from,
            'price_to':        self.price_to,
            'interval':        self.interval,
            'discord_webhook': self.discord_webhook or '',
            'images':          images,
            'image_count':     len(images),
            'created_at':      self.created_at.isoformat() if self.created_at else None,
        }
