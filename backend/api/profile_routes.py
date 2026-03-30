"""
Watch Profile API Routes
========================
GET    /api/profiles              → Liste aller Profile
POST   /api/profiles              → Neues Profil erstellen
GET    /api/profiles/<id>         → Einzelnes Profil
PUT    /api/profiles/<id>         → Profil updaten
DELETE /api/profiles/<id>         → Profil löschen

POST   /api/profiles/<id>/images  → Bild hochladen (multipart/form-data, field: 'image')
DELETE /api/profiles/<id>/images/<filename> → Bild löschen
GET    /api/profiles/<id>/images/<filename> → Bild abrufen (für <img src=...>)
"""

import os
import json
import logging
from flask import Blueprint, jsonify, request, send_file, abort
from werkzeug.utils import secure_filename
from database.db     import db
from database.models import WatchProfile
from config          import Config

logger      = logging.getLogger(__name__)
profile_bp  = Blueprint('profiles', __name__)

IMAGES_BASE = Config.TRAINING_IMAGES_DIR   # Absoluter Pfad – kein Auflösungs-Bug
ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def _image_dir(profile_id: int) -> str:
    d = os.path.join(IMAGES_BASE, str(profile_id))
    os.makedirs(d, exist_ok=True)
    return d


# ── CRUD ──────────────────────────────────────────────────────────

@profile_bp.route('', methods=['GET'])
def list_profiles():
    profiles = WatchProfile.query.order_by(WatchProfile.created_at.desc()).all()
    return jsonify([p.to_dict() for p in profiles])


@profile_bp.route('', methods=['POST'])
def create_profile():
    data = request.json or {}
    if not data.get('name', '').strip():
        return jsonify({'error': 'Name fehlt'}), 400

    p = WatchProfile(
        name             = data['name'].strip(),
        brands_json      = json.dumps([b.strip() for b in data.get('brands', []) if b.strip()]),
        keywords_json    = json.dumps([k.strip() for k in data.get('keywords', []) if k.strip()]),
        price_from       = data.get('price_from'),
        price_to         = data.get('price_to'),
        interval         = max(int(data.get('interval', 15)), 10),
        discord_webhook  = data.get('discord_webhook', '').strip(),
    )
    db.session.add(p)
    db.session.commit()
    logger.info(f"Profil erstellt: {p.name} (id={p.id})")
    return jsonify(p.to_dict()), 201


@profile_bp.route('/<int:pid>', methods=['GET'])
def get_profile(pid):
    p = WatchProfile.query.get_or_404(pid)
    return jsonify(p.to_dict())


@profile_bp.route('/<int:pid>', methods=['PUT'])
def update_profile(pid):
    p    = WatchProfile.query.get_or_404(pid)
    data = request.json or {}

    if 'name'            in data: p.name             = data['name'].strip()
    if 'brands'          in data: p.brands_json      = json.dumps([b.strip() for b in data['brands']   if b.strip()])
    if 'keywords'        in data: p.keywords_json    = json.dumps([k.strip() for k in data['keywords'] if k.strip()])
    if 'price_from'      in data: p.price_from       = data['price_from']
    if 'price_to'        in data: p.price_to         = data['price_to']
    if 'interval'        in data: p.interval         = max(int(data['interval']), 10)
    if 'discord_webhook' in data: p.discord_webhook  = data['discord_webhook'].strip()

    db.session.commit()
    return jsonify(p.to_dict())


@profile_bp.route('/<int:pid>', methods=['DELETE'])
def delete_profile(pid):
    p = WatchProfile.query.get_or_404(pid)
    # Bilder löschen
    img_dir = os.path.join(IMAGES_BASE, str(pid))
    if os.path.isdir(img_dir):
        import shutil
        shutil.rmtree(img_dir)
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


# ── Images ────────────────────────────────────────────────────────

@profile_bp.route('/<int:pid>/images', methods=['POST'])
def upload_image(pid):
    WatchProfile.query.get_or_404(pid)   # 404 wenn Profil nicht existiert

    if 'image' not in request.files:
        return jsonify({'error': 'Kein Bild im Request (field: image)'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'error': 'Leerer Dateiname'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({'error': f'Nicht erlaubtes Format: {ext}. Erlaubt: {ALLOWED_EXT}'}), 400

    filename = secure_filename(file.filename)
    # Eindeutigen Namen sicherstellen
    img_dir  = _image_dir(pid)
    base, ex = os.path.splitext(filename)
    counter  = 1
    dest     = os.path.join(img_dir, filename)
    while os.path.exists(dest):
        filename = f"{base}_{counter}{ex}"
        dest     = os.path.join(img_dir, filename)
        counter += 1

    file.save(dest)
    logger.info(f"Bild hochgeladen: {filename} für Profil {pid}")
    return jsonify({'success': True, 'filename': filename}), 201


@profile_bp.route('/<int:pid>/images/<filename>', methods=['DELETE'])
def delete_image(pid, filename):
    WatchProfile.query.get_or_404(pid)
    safe = secure_filename(filename)
    path = os.path.join(IMAGES_BASE, str(pid), safe)
    if not os.path.exists(path):
        return jsonify({'error': 'Bild nicht gefunden'}), 404
    os.remove(path)
    return jsonify({'success': True})


@profile_bp.route('/<int:pid>/images/<filename>', methods=['GET'])
def serve_image(pid, filename):
    safe    = secure_filename(filename)
    img_dir = os.path.join(IMAGES_BASE, str(pid))
    path    = os.path.join(img_dir, safe)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)
