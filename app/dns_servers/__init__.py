# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("dns_servers", __name__, url_prefix="/dns-servers")

from app.dns_servers import routes  # noqa: E402,F401
