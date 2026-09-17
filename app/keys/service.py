import datetime

from bson import ObjectId

from app.extensions import get_db
from app.utils.crypto import encrypt_private_key, generate_keypair


def generate_and_store_key(owner_type, owner_id):
    public_b64, private_b64 = generate_keypair()
    return _store_key(owner_type, owner_id, public_b64, private_b64)


def store_provided_key(owner_type, owner_id, public_b64, private_b64):
    return _store_key(owner_type, owner_id, public_b64, private_b64)


def _store_key(owner_type, owner_id, public_b64, private_b64):
    db = get_db()
    doc = {
        "public_key": public_b64,
        "private_key": encrypt_private_key(private_b64),
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "active": True,
        "created_at": datetime.datetime.utcnow(),
    }
    result = db.keys.insert_one(doc)
    return str(result.inserted_id)


def deactivate_key(key_id):
    get_db().keys.update_one({"_id": ObjectId(key_id)}, {"$set": {"active": False}})
