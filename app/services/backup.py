# Copyright © 2026 Isaac Behrens. All rights reserved.

import io
import logging
import os
import re
import zipfile
from datetime import datetime, timedelta

from bson import json_util
from flask import current_app

from app.extensions import get_db

log = logging.getLogger(__name__)

COLLECTIONS = [
    "hosts",
    "clients",
    "networks",
    "keys",
    "dns_servers",
    "allowed_ips",
    "users",
]

SNAPSHOT_NAME_RE = re.compile(r"^wireguard-manager-backup-\d{8}-\d{6}(-\d+)?\.zip$")

_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def create_backup_bytes():
    """Dump every collection to a .zip archive in memory and return its bytes."""
    db = get_db()
    counts = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in COLLECTIONS:
            docs = list(db[name].find())
            counts[name] = len(docs)
            zf.writestr(f"{name}.json", json_util.dumps(docs, indent=2))
        manifest = {
            "app": "wireguard-network-manager",
            "version": current_app.config["VERSION"],
            "created_at": datetime.utcnow().isoformat() + "Z",
            "collections": counts,
        }
        zf.writestr("manifest.json", json_util.dumps(manifest, indent=2))
    return buf.getvalue()


def backup_filename():
    return f"wireguard-manager-backup-{datetime.utcnow():%Y%m%d-%H%M%S}.zip"


def write_snapshot(directory):
    """Write a fresh backup archive into directory; return the full path."""
    os.makedirs(directory, exist_ok=True)
    data = create_backup_bytes()
    base = backup_filename()
    path = os.path.join(directory, base)
    counter = 1
    while True:
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            counter += 1
            path = os.path.join(directory, base[:-4] + f"-{counter}.zip")


def list_snapshots(directory):
    """List snapshot files in directory, newest first. Missing dir -> []."""
    try:
        names = sorted(
            (n for n in os.listdir(directory) if SNAPSHOT_NAME_RE.match(n)),
            reverse=True,
        )
    except OSError:
        return []
    snapshots = []
    for name in names:
        try:
            st = os.stat(os.path.join(directory, name))
        except OSError:
            continue
        snapshots.append(
            {
                "name": name,
                "size": st.st_size,
                "created": datetime.fromtimestamp(st.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
    return snapshots


def prune_snapshots(directory, retention):
    """Delete oldest snapshots beyond retention count; return deleted names."""
    snapshots = list_snapshots(directory)
    deleted = []
    for snap in snapshots[retention:]:
        try:
            os.remove(os.path.join(directory, snap["name"]))
            deleted.append(snap["name"])
        except OSError:
            log.warning("Could not delete old snapshot %s", snap["name"])
    return deleted


def read_manifest(data):
    """Validate that data is a backup archive; return its manifest dict."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid backup archive (not a zip file).")
    with zf:
        try:
            raw = zf.read("manifest.json")
        except KeyError:
            raise ValueError("Not a valid backup archive (missing manifest.json).")
    try:
        manifest = json_util.loads(raw)
    except ValueError:
        raise ValueError("Not a valid backup archive (corrupt manifest.json).")
    if not isinstance(manifest, dict) or "collections" not in manifest:
        raise ValueError("Not a valid backup archive (bad manifest.json).")
    return manifest


def restore_from_bytes(data):
    """Replace every collection present in the archive. Returns a summary dict."""
    manifest = read_manifest(data)
    db = get_db()
    restored = {}
    skipped = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        for name in COLLECTIONS:
            entry = f"{name}.json"
            if entry not in names:
                skipped.append(name)
                continue
            docs = json_util.loads(zf.read(entry))
            db[name].delete_many({})
            if docs:
                db[name].insert_many(docs)
            restored[name] = len(docs)
    return {"restored": restored, "skipped": skipped, "manifest": manifest}


def safety_snapshot():
    """Write a pre-restore snapshot to BACKUP_DIR; None if the dir is unusable."""
    try:
        return write_snapshot(current_app.config["BACKUP_DIR"])
    except OSError:
        log.warning("Could not write pre-restore safety snapshot", exc_info=True)
        return None


def parse_schedule(spec):
    """Parse BACKUP_SCHEDULE ("sunday 00:00", or "off").

    Returns (weekday, hour, minute) with weekday 0=Monday..6=Sunday, or None
    when disabled. Raises ValueError on an unparseable spec.
    """
    spec = (spec or "").strip().lower()
    if not spec or spec == "off":
        return None
    parts = spec.split()
    if len(parts) != 2 or parts[0] not in _DAYS:
        raise ValueError(f"Invalid BACKUP_SCHEDULE: {spec!r} (want e.g. 'sunday 00:00' or 'off')")
    time_parts = parts[1].split(":")
    if len(time_parts) != 2:
        raise ValueError(f"Invalid BACKUP_SCHEDULE time: {parts[1]!r} (want HH:MM)")
    try:
        hour, minute = int(time_parts[0]), int(time_parts[1])
    except ValueError:
        raise ValueError(f"Invalid BACKUP_SCHEDULE time: {parts[1]!r} (want HH:MM)")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid BACKUP_SCHEDULE time: {parts[1]!r} (want HH:MM)")
    return (_DAYS[parts[0]], hour, minute)


def next_run(now, weekday, hour, minute):
    """Next weekly occurrence of weekday/hour/minute strictly after now."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate += timedelta(days=(weekday - now.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def describe_schedule(spec):
    """Human-readable schedule for the backup page."""
    try:
        parsed = parse_schedule(spec)
    except ValueError:
        return "invalid (check BACKUP_SCHEDULE)"
    if parsed is None:
        return "disabled"
    weekday, hour, minute = parsed
    day = [d for d, i in _DAYS.items() if i == weekday][0].capitalize()
    return f"weekly on {day} at {hour:02d}:{minute:02d} (container time)"
