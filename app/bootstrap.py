# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import current_app
from werkzeug.security import generate_password_hash

from app.extensions import get_db


def ensure_admin_user():
    db = get_db()
    if db.users.count_documents({}) > 0:
        return
    db.users.insert_one(
        {
            "username": current_app.config["ADMIN_USERNAME"],
            "password_hash": generate_password_hash(current_app.config["ADMIN_PASSWORD"]),
        }
    )
