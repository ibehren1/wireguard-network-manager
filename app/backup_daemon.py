# Copyright © 2026 Isaac Behrens. All rights reserved.

import logging
import os
import time
from datetime import datetime

from app import create_app
from app.services import backup as backup_svc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_SLEEP_STEP = 5


def _idle():
    while True:
        time.sleep(3600)


def main():
    if os.environ.get("TZ"):
        time.tzset()

    app = create_app()
    spec = app.config["BACKUP_SCHEDULE"]
    directory = app.config["BACKUP_DIR"]
    retention = app.config["BACKUP_RETENTION"]

    try:
        schedule = backup_svc.parse_schedule(spec)
    except ValueError as exc:
        log.error("%s — scheduled backups idling.", exc)
        _idle()
        return
    if schedule is None:
        log.info("Scheduled backups disabled (BACKUP_SCHEDULE=%r); idling.", spec)
        _idle()
        return

    weekday, hour, minute = schedule
    log.info(
        "Scheduled backups: %s; dir=%s retention=%s",
        backup_svc.describe_schedule(spec),
        directory,
        retention,
    )
    while True:
        target = backup_svc.next_run(datetime.now(), weekday, hour, minute)
        log.info("Next backup at %s", target.strftime("%Y-%m-%d %H:%M:%S"))
        while datetime.now() < target:
            time.sleep(_SLEEP_STEP)
        with app.app_context():
            try:
                path = backup_svc.write_snapshot(directory)
                log.info("Wrote snapshot %s", path)
                deleted = backup_svc.prune_snapshots(directory, retention)
                for name in deleted:
                    log.info("Pruned old snapshot %s", name)
            except OSError:
                log.exception("Snapshot failed; will retry at the next scheduled time")


if __name__ == "__main__":
    main()
