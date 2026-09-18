import datetime

from bson import ObjectId

from app.extensions import get_db
from app.utils.crypto import encrypt_private_key, generate_keypair


def generate_and_store_key(owner_type, owner_id):
    public_b64, private_b64 = generate_keypair()
    return _store_key(owner_type, owner_id, public_b64, private_b64, active=True)


def store_provided_key(owner_type, owner_id, public_b64, private_b64):
    return _store_key(owner_type, owner_id, public_b64, private_b64, active=True)


def create_unassigned_key(public_b64=None, private_b64=None):
    if public_b64 is None or private_b64 is None:
        public_b64, private_b64 = generate_keypair()
    return _store_key(None, None, public_b64, private_b64, active=False)


def _store_key(owner_type, owner_id, public_b64, private_b64, active):
    db = get_db()
    doc = {
        "public_key": public_b64,
        "private_key": encrypt_private_key(private_b64),
        "owner_type": owner_type,
        "owner_id": str(owner_id) if owner_id is not None else None,
        "active": active,
        "created_at": datetime.datetime.utcnow(),
    }
    result = db.keys.insert_one(doc)
    return str(result.inserted_id)


def deactivate_key(key_id):
    get_db().keys.update_one({"_id": ObjectId(key_id)}, {"$set": {"active": False}})


def assign_key_to_owner(key_id, owner_type, owner_id):
    """Assign an unassigned key as the active key for a Host/Client, retiring
    whatever active key that owner currently has."""
    db = get_db()
    collection = db.hosts if owner_type == "host" else db.clients
    owner = collection.find_one({"_id": ObjectId(owner_id)})
    if owner and owner.get("active_key_id"):
        deactivate_key(owner["active_key_id"])

    db.keys.update_one(
        {"_id": ObjectId(key_id)},
        {"$set": {"owner_type": owner_type, "owner_id": str(owner_id), "active": True}},
    )
    collection.update_one({"_id": ObjectId(owner_id)}, {"$set": {"active_key_id": key_id}})


def delete_unassigned_key(key_id):
    get_db().keys.delete_one({"_id": ObjectId(key_id), "owner_type": None})
