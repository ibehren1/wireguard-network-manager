import datetime

from bson import ObjectId

from app.extensions import get_db
from app.utils.crypto import derive_public_key, encrypt_private_key, generate_keypair


def generate_and_store_key(name):
    public_b64, private_b64 = generate_keypair()
    return _store_key(name, public_b64, private_b64)


def store_provided_key(name, private_b64):
    public_b64 = derive_public_key(private_b64)
    return _store_key(name, public_b64, private_b64)


def create_unassigned_key(name, private_b64=None):
    """Create a standalone key not yet attached to any Host/Client."""
    if private_b64:
        public_b64 = derive_public_key(private_b64)
    else:
        public_b64, private_b64 = generate_keypair()
    return _store_key(name, public_b64, private_b64)


def _store_key(name, public_b64, private_b64):
    db = get_db()
    doc = {
        "name": name,
        "public_key": public_b64,
        "private_key": encrypt_private_key(private_b64),
        "created_at": datetime.datetime.utcnow(),
    }
    result = db.keys.insert_one(doc)
    return str(result.inserted_id)


def assign_key_to_owner(key_id, owner_type, owner_id):
    """Point a Host/Client's active_key_id at the given key.

    A key can be the active key for more than one Host/Client at a time
    (not recommended, but not prevented) — this just sets the pointer, it
    does not touch the key document or any other owner referencing it.
    """
    db = get_db()
    collection = db.hosts if owner_type == "host" else db.clients
    collection.update_one({"_id": ObjectId(owner_id)}, {"$set": {"active_key_id": str(key_id)}})


def key_usages(key_id):
    """Return every Host/Client currently using this key as its active key.

    Usage is derived (not stored): a key is "in use" by any Host/Client
    whose active_key_id matches. Each entry is
    {"type": "host"/"client", "id": str, "name": str}.
    """
    db = get_db()
    key_id = str(key_id)
    usages = [
        {"type": "host", "id": str(h["_id"]), "name": h.get("name") or "(unnamed host)"}
        for h in db.hosts.find({"active_key_id": key_id}).sort("name", 1)
    ]
    usages += [
        {"type": "client", "id": str(c["_id"]), "name": c.get("name") or "(unnamed client)"}
        for c in db.clients.find({"active_key_id": key_id}).sort("name", 1)
    ]
    return usages


def delete_key_if_unused(key_id):
    """Delete a key only if no Host/Client currently references it.

    Returns True if the key was deleted, False if it's still in use (or
    doesn't exist).
    """
    if key_usages(key_id):
        return False
    result = get_db().keys.delete_one({"_id": ObjectId(key_id)})
    return result.deleted_count > 0
