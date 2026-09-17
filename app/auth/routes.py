from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from app.auth import bp
from app.auth.forms import LoginForm
from app.auth.models import User
from app.extensions import get_db


@bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        doc = get_db().users.find_one({"username": form.username.data})
        if doc and check_password_hash(doc["password_hash"], form.password.data):
            login_user(User(doc))
            next_url = request.args.get("next") or url_for("main.dashboard")
            return redirect(next_url)
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
