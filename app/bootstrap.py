# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import current_app
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

from app.extensions import get_db


def _dedupe_usernames(db):
    """Drop duplicate-username docs (keep oldest) left by the pre-index seeding race."""
    dupes = db.users.aggregate(
        [
            {"$group": {"_id": "$username", "ids": {"$push": "$_id"}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
        ]
    )
    for dupe in dupes:
        db.users.delete_many({"_id": {"$in": sorted(dupe["ids"])[1:]}})


def ensure_admin_user():
    db = get_db()
    # Unique index so the concurrent create_app() calls at container boot
    # (2 gunicorn workers + the backup daemon) can't seed duplicate admins.
    # Existing DBs may already hold duplicates from that race — dedupe first.
    _dedupe_usernames(db)
    db.users.create_index("username", unique=True)
    if db.users.count_documents({}) > 0:
        return
    try:
        db.users.insert_one(
            {
                "username": current_app.config["ADMIN_USERNAME"],
                "password_hash": generate_password_hash(current_app.config["ADMIN_PASSWORD"]),
            }
        )
    except DuplicateKeyError:
        pass  # another process won the seeding race
