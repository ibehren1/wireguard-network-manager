from flask import Flask

from app.config import Config
from app.extensions import init_fernet, init_mongo, login_manager


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    init_mongo(app)
    init_fernet(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from app.auth import bp as auth_bp
    from app.auth import models as auth_models  # noqa: F401 registers user_loader
    from app.clients import bp as clients_bp
    from app.hosts import bp as hosts_bp
    from app.keys import bp as keys_bp
    from app.main import bp as main_bp
    from app.networks import bp as networks_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(networks_bp)
    app.register_blueprint(hosts_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(keys_bp)

    from app.bootstrap import ensure_admin_user

    with app.app_context():
        ensure_admin_user()

    return app
