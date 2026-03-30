from flask_sqlalchemy import SQLAlchemy

# Zentrale SQLAlchemy-Instanz – wird in app.py mit der App verbunden
# In V2 einfach DATABASE_URI in config.py auf PostgreSQL umstellen, alles andere bleibt
db = SQLAlchemy()
