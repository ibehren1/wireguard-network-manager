# Copyright © 2026 Isaac Behrens. All rights reserved.

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
    """Point a Client's active_key_id at the given key.

    A key can be the active key for more than one Host interface/Client at a
    time (not recommended, but not prevented) — this just sets the pointer,
    it does not touch the key document or any other owner referencing it.

    Hosts no longer have a single active_key_id (keys are per-interface) —
    see assign_key_to_host_interface below for that case.
    """
    db = get_db()
    collection = db.hosts if owner_type == "host" else db.clients
    collection.update_one({"_id": ObjectId(owner_id)}, {"$set": {"active_key_id": str(key_id)}})


def assign_key_to_host_interface(key_id, host_id, network_id):
    """Point one of a Host's network_memberships (interfaces) at the given key.

    Same "not exclusively owned" policy as assign_key_to_owner — this just
    repoints that interface's active_key_id, leaving the key document and any
    other usage alone.
    """
    db = get_db()
    db.hosts.update_one(
        {"_id": ObjectId(host_id)},
        {"$set": {"network_memberships.$[elem].active_key_id": str(key_id)}},
        array_filters=[{"elem.network_id": network_id}],
    )


def key_usages(key_id):
    """Return every Host interface/Client currently using this key as its
    active key.

    Usage is derived (not stored): a key is "in use" by any Host interface
    (network_memberships entry) or Client whose active_key_id matches. Each
    entry is either {"type": "host_interface", "host_id": str, "network_id":
    str, "name": str} (one per matching interface — a host could in theory
    have the same key on more than one interface) or
    {"type": "client", "id": str, "name": str}.
    """
    db = get_db()
    key_id = str(key_id)
    usages = []
    for h in db.hosts.find({"network_memberships.active_key_id": key_id}).sort("name", 1):
        for m in h.get("network_memberships", []):
            if m.get("active_key_id") == key_id:
                interface_name = m.get("interface_name") or "wg?"
                usages.append({
                    "type": "host_interface",
                    "host_id": str(h["_id"]),
                    "network_id": m["network_id"],
                    "name": f"{h.get('name') or '(unnamed host)'} ({interface_name})",
                })
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
