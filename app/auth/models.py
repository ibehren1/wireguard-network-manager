# Copyright © 2026 Isaac Behrens. All rights reserved.

from bson import ObjectId
from flask_login import UserMixin

from app.extensions import get_db, login_manager


class User(UserMixin):
    def __init__(self, doc):
        self.doc = doc

    def get_id(self):
        return str(self.doc["_id"])

    @property
    def username(self):
        return self.doc["username"]

    @property
    def password_hash(self):
        return self.doc["password_hash"]


@login_manager.user_loader
def load_user(user_id):
    doc = get_db().users.find_one({"_id": ObjectId(user_id)})
    return User(doc) if doc else None
