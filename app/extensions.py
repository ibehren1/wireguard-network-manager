# Copyright © 2026 Isaac Behrens. All rights reserved.

from cryptography.fernet import Fernet
from flask_login import LoginManager
from pymongo import MongoClient

login_manager = LoginManager()

_mongo_client = None
_db = None
_fernet = None


def init_mongo(app):
    global _mongo_client, _db
    _mongo_client = MongoClient(app.config["MONGO_URI"])
    _db = _mongo_client.get_default_database()


def init_fernet(app):
    global _fernet
    key = app.config.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    _fernet = Fernet(key.encode())


def get_db():
    if _db is None:
        raise RuntimeError("Mongo has not been initialized yet.")
    return _db


def get_fernet():
    if _fernet is None:
        raise RuntimeError("Fernet has not been initialized yet.")
    return _fernet
