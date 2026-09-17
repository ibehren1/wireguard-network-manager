from bson import ObjectId
from flask import render_template
from flask_login import login_required

from app.extensions import get_db
from app.keys import bp


@bp.route("/")
@login_required
def list_keys():
    db = get_db()
    keys = list(db.keys.find().sort("created_at", -1))
    for key in keys:
        owner_name = None
        if key["owner_type"] == "host":
            owner = db.hosts.find_one({"_id": ObjectId(key["owner_id"])})
            owner_name = owner["name"] if owner else "(deleted host)"
        elif key["owner_type"] == "client":
            owner = db.clients.find_one({"_id": ObjectId(key["owner_id"])})
            owner_name = owner["name"] if owner else "(deleted client)"
        key["owner_name"] = owner_name
    return render_template("keys/list.html", keys=keys)
