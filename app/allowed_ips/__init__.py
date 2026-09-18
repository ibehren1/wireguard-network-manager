# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("allowed_ips", __name__, url_prefix="/allowed-ips")

from app.allowed_ips import routes  # noqa: E402,F401
