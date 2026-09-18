# Copyright © 2026 Isaac Behrens. All rights reserved.

from bson import ObjectId
from flask import flash, redirect, render_template, url_for
from flask_login import login_required

from app.extensions import get_db
from app.hosts.forms import INTERFACE_TYPE_LABELS
from app.keys import bp
from app.keys.forms import KeyAssignForm, KeyCreateForm, KeyEditForm
from app.keys.service import (
    assign_key_to_host_interface,
    assign_key_to_owner,
    create_unassigned_key,
    delete_key_if_unused,
    key_usages,
)
from app.utils.crypto import decrypt_private_key, is_valid_wg_key


@bp.route("/")
@login_required
def list_keys():
    db = get_db()
    keys = list(db.keys.find().sort("created_at", -1))
    for key in keys:
        key["usages"] = key_usages(key["_id"])
    return render_template("keys/list.html", keys=keys)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_key():
    form = KeyCreateForm()
    if form.validate_on_submit():
        private_key = (form.private_key.data or "").strip()
        if private_key:
            if not is_valid_wg_key(private_key):
                flash("Private key must be a valid base64-encoded 32-byte WireGuard key.", "danger")
                return render_template("keys/form.html", form=form)
            create_unassigned_key(form.name.data, private_key)
        else:
            create_unassigned_key(form.name.data)
        flash("Key created. Attach it to a Host or Client to use it.", "success")
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

    form = KeyAssignForm()
    hosts = list(db.hosts.find().sort("name", 1))
    clients = list(db.clients.find().sort("name", 1))

    choices = []
    target_names = {}
    for h in hosts:
        for m in h.get("network_memberships", []):
            interface_type = m.get("interface_type", "client")
            if interface_type == "client_non_wg":
                continue  # non-WireGuard interfaces never have a key
            interface_name = m.get("interface_name") or "wg?"
            value = f"hostiface:{h['_id']}:{m['network_id']}"
            type_label = INTERFACE_TYPE_LABELS.get(interface_type, interface_type)
            choices.append((value, f"Host: {h['name']} — {interface_name} ({type_label})"))
            target_names[value] = f"{h['name']} ({interface_name})"
    choices += [(f"client:{c['_id']}", f"Client: {c['name']}") for c in clients]
    target_names.update({f"client:{c['_id']}": c.get("name", "") for c in clients})
    form.target.choices = choices

    if form.validate_on_submit():
        target = form.target.data
        prior_usages = key_usages(key_id)
        if target.startswith("hostiface:"):
            _, host_id, network_id = target.split(":", 2)
            assign_key_to_host_interface(key_id, host_id, network_id)
        else:
            owner_type, owner_id = target.split(":", 1)
            assign_key_to_owner(key_id, owner_type, owner_id)
        flash("Key assigned.", "success")
        if prior_usages:
            used_by = ", ".join(u["name"] for u in prior_usages)
            target_name = target_names.get(form.target.data, "the selected target")
            flash(
                f"This key is already used by: {used_by} — it will now also be used by {target_name}. "
                "This isn't recommended (WireGuard keys are meant to be unique per peer).",
                "warning",
            )
        return redirect(url_for("keys.list_keys"))

    return render_template("keys/assign_form.html", form=form, key=key)


@bp.route("/<key_id>/edit", methods=["GET", "POST"])
@login_required
def edit_key(key_id):
    db = get_db()
    key = db.keys.find_one({"_id": ObjectId(key_id)})
    if not key:
        flash("Key not found.", "danger")
        return redirect(url_for("keys.list_keys"))

    form = KeyEditForm()
    if form.validate_on_submit():
        db.keys.update_one({"_id": ObjectId(key_id)}, {"$set": {"name": form.name.data}})
        flash("Key updated.", "success")
        return redirect(url_for("keys.list_keys"))

    if not form.is_submitted():
        form.name.data = key.get("name")

    private_key = decrypt_private_key(key["private_key"]) if key.get("private_key") else None
    return render_template("keys/edit_form.html", form=form, key=key, private_key=private_key)


@bp.route("/<key_id>/delete", methods=["POST"])
@login_required
def delete_key(key_id):
    if delete_key_if_unused(key_id):
        flash("Key deleted.", "success")
    else:
        flash("Cannot delete a key that is still in use by a Host or Client.", "danger")
    return redirect(url_for("keys.list_keys"))
