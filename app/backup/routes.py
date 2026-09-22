# Copyright © 2026 Isaac Behrens. All rights reserved.

import os

from flask import (
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user, login_required, logout_user

from app.backup import bp
from app.backup.forms import ConfirmRestoreForm, RestoreUploadForm
from app.extensions import get_db
from app.services import backup as backup_svc


def _backup_dir():
    return current_app.config["BACKUP_DIR"]


def _snapshot_path_or_404(name):
    if not backup_svc.SNAPSHOT_NAME_RE.match(name):
        abort(404)
    return os.path.join(_backup_dir(), name)


def _current_counts():
    db = get_db()
    return {name: db[name].count_documents({}) for name in backup_svc.COLLECTIONS}


def _render_confirm(source, data):
    try:
        manifest = backup_svc.read_manifest(data)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backup.index"))
    form = ConfirmRestoreForm(data={"source": source})
    return render_template(
        "backup/confirm.html",
        form=form,
        manifest=manifest,
        current_counts=_current_counts(),
        collections=backup_svc.COLLECTIONS,
    )


@bp.route("/")
@login_required
def index():
    schedule = backup_svc.describe_schedule(current_app.config["BACKUP_SCHEDULE"])
    return render_template(
        "backup/index.html",
        snapshots=backup_svc.list_snapshots(_backup_dir()),
        form=RestoreUploadForm(),
        schedule=schedule,
        retention=current_app.config["BACKUP_RETENTION"],
    )


@bp.route("/create", methods=["POST"])
@login_required
def create():
    directory = _backup_dir()
    try:
        path = backup_svc.write_snapshot(directory)
    except OSError:
        flash(f"Could not write backup to {directory}.", "danger")
        return redirect(url_for("backup.index"))
    deleted = backup_svc.prune_snapshots(
        directory, current_app.config["BACKUP_RETENTION"]
    )
    flash(f"Backup created: {os.path.basename(path)}", "success")
    if deleted:
        flash(f"Pruned old backups: {', '.join(deleted)}", "success")
    return redirect(url_for("backup.index"))


@bp.route("/snapshots/<name>/download")
@login_required
def download_snapshot(name):
    path = _snapshot_path_or_404(name)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        flash("Backup not found.", "danger")
        return redirect(url_for("backup.index"))
    return Response(
        data,
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


@bp.route("/snapshots/<name>/delete", methods=["POST"])
@login_required
def delete_snapshot(name):
    path = _snapshot_path_or_404(name)
    try:
        os.remove(path)
        flash(f"Backup deleted: {name}", "success")
    except OSError:
        flash("Backup not found.", "danger")
    return redirect(url_for("backup.index"))


@bp.route("/snapshots/<name>/restore", methods=["POST"])
@login_required
def restore_snapshot(name):
    path = _snapshot_path_or_404(name)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        flash("Backup not found.", "danger")
        return redirect(url_for("backup.index"))
    return _render_confirm(f"snapshot:{name}", data)


@bp.route("/restore", methods=["GET"])
@login_required
def restore():
    return redirect(url_for("backup.index"))


@bp.route("/restore", methods=["POST"])
@login_required
def restore_upload():
    form = RestoreUploadForm()
    if not form.validate_on_submit():
        flash("Choose a backup archive to upload.", "danger")
        return redirect(url_for("backup.index"))
    data = form.archive.data.read()
    try:
        backup_svc.read_manifest(data)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backup.index"))
    pending = os.path.join(
        _backup_dir(), f".restore-pending-{current_user.get_id()}.zip"
    )
    try:
        os.makedirs(_backup_dir(), exist_ok=True)
        with open(pending, "wb") as f:
            f.write(data)
    except OSError:
        flash(f"Could not stage upload in {_backup_dir()}.", "danger")
        return redirect(url_for("backup.index"))
    return _render_confirm(f"pending:{current_user.get_id()}", data)


@bp.route("/restore/confirm", methods=["POST"])
@login_required
def restore_confirm():
    form = ConfirmRestoreForm()
    if not form.validate_on_submit():
        flash("Invalid restore request.", "danger")
        return redirect(url_for("backup.index"))

    source = form.source.data
    pending_path = None
    if source.startswith("snapshot:"):
        name = source[len("snapshot:"):]
        path = _snapshot_path_or_404(name)
    elif source.startswith("pending:"):
        uid = source[len("pending:"):]
        if uid != current_user.get_id():
            abort(404)
        pending_path = os.path.join(_backup_dir(), f".restore-pending-{uid}.zip")
        path = pending_path
    else:
        abort(404)

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        flash("Backup archive is no longer available — start the restore again.", "danger")
        return redirect(url_for("backup.index"))

    try:
        backup_svc.safety_snapshot()
        summary = backup_svc.restore_from_bytes(data)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backup.index"))
    finally:
        if pending_path:
            try:
                os.remove(pending_path)
            except OSError:
                pass

    counts = ", ".join(f"{k}: {v}" for k, v in summary["restored"].items())
    flash(f"Restore complete ({counts}).", "success")
    if summary["skipped"]:
        flash(f"Collections not present in the archive: {', '.join(summary['skipped'])}", "warning")
    logout_user()
    flash("You have been logged out — log in with the credentials from the restored backup.", "warning")
    return redirect(url_for("auth.login"))
