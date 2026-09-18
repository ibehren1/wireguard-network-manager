# Copyright © 2026 Isaac Behrens. All rights reserved.

import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")


def _read_version():
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


class Config:
    VERSION = _read_version()
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/wireguard_manager")
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
