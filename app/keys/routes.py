from bson import ObjectId
from flask import flash, redirect, render_template, url_for
from flask_login import login_required

from app.extensions import get_db
from app.keys import bp
from app.keys.forms import KeyAssignForm, KeyCreateForm
from app.keys.service import assign_key_to_owner, create_unassigned_key, delete_unassigned_key
from app.utils.crypto import is_valid_wg_key, keypair_matches


def _owner_label(db, key):
    if key["owner_type"] == "host":
        owner = db.hosts.find_one({"_id": ObjectId(key["owner_id"])})
        return (owner["name"] if owner else "(deleted host)"), (str(owner["_id"]) if owner else None)
    if key["owner_type"] == "client":
        owner = db.clients.find_one({"_id": ObjectId(key["owner_id"])})
        return (owner["name"] if owner else "(deleted client)"), (str(owner["_id"]) if owner else None)
    return None, None


@bp.route("/")
@login_required
def list_keys():
    db = get_db()
    keys = list(db.keys.find().sort("created_at", -1))
    for key in keys:
        key["owner_name"], key["owner_url_id"] = _owner_label(db, key)
    return render_template("keys/list.html", keys=keys)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_key():
    form = KeyCreateForm()
    if form.validate_on_submit():
        if form.key_source.data == "provide":
            public_key = (form.public_key.data or "").strip()
            private_key = (form.private_key.data or "").strip()
            if not is_valid_wg_key(public_key) or not is_valid_wg_key(private_key):
                flash("Public/private key must be valid base64-encoded 32-byte WireGuard keys.", "danger")
                return render_template("keys/form.html", form=form)
            if not keypair_matches(public_key, private_key):
                flash("Provided public key does not match the private key.", "danger")
                return render_template("keys/form.html", form=form)
            create_unassigned_key(public_key, private_key)
        else:
            create_unassigned_key()
        flash("Key created. It's unassigned until you attach it to a Host or Client.", "success")
        return redirect(url_for("keys.list_keys"))
    return render_template("keys/form.html", form=form)


@bp.route("/<key_id>/assign", methods=["GET", "POST"])
@login_required
def assign_key(key_id):
    db = get_db()
    key = db.keys.find_one({"_id": ObjectId(key_id)})
    if not key:
        flash("Key not found.", "danger")
        return redirect(url_for("keys.list_keys"))
    if key["owner_type"] is not None:
        flash("Key is already assigned.", "danger")
        return redirect(url_for("keys.list_keys"))

    form = KeyAssignForm()
    choices = [(f"host:{h['_id']}", f"Host: {h['name']}") for h in db.hosts.find().sort("name", 1)]
    choices += [(f"client:{c['_id']}", f"Client: {c['name']}") for c in db.clients.find().sort("name", 1)]
    form.target.choices = choices

    if form.validate_on_submit():
        owner_type, owner_id = form.target.data.split(":", 1)
        assign_key_to_owner(key_id, owner_type, owner_id)
        flash("Key assigned.", "success")
        return redirect(url_for("keys.list_keys"))

    return render_template("keys/assign_form.html", form=form, key=key)


@bp.route("/<key_id>/delete", methods=["POST"])
@login_required
def delete_key(key_id):
    db = get_db()
    key = db.keys.find_one({"_id": ObjectId(key_id)})
    if key and key["owner_type"] is not None:
        flash("Cannot delete a key that is assigned to a Host or Client.", "danger")
        return redirect(url_for("keys.list_keys"))
    delete_unassigned_key(key_id)
    flash("Key deleted.", "success")
    return redirect(url_for("keys.list_keys"))
