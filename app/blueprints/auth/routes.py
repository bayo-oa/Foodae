from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db, limiter
from app.models import User, Role

auth_bp = Blueprint("auth", __name__, url_prefix="/auth", template_folder="../../templates/auth")


def _role_home(role):
    return {
        Role.CUSTOMER: "customer.homepage",
        Role.VENDOR: "vendor.dashboard",
        Role.RIDER: "rider.dashboard",
        Role.ADMIN: "admin.dashboard",
    }.get(role, "customer.homepage")


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for(_role_home(current_user.role)))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", Role.CUSTOMER)

        errors = []
        if not email or "@" not in email:
            errors.append("A valid email is required.")
        if not full_name:
            errors.append("Full name is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if role not in Role.SELF_REGISTERABLE:
            errors.append("Invalid role selected.")
        if User.query.filter_by(email=email).first():
            errors.append("An account with this email already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/register.html", form=request.form)

        user = User(email=email, full_name=full_name, phone=phone, role=role, is_active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Welcome! Your account has been created.", "success")

        if role == Role.VENDOR:
            return redirect(url_for("vendor.create_restaurant"))
        return redirect(url_for(_role_home(role)))

    return render_template("auth/register.html", form={})


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for(_role_home(current_user.role)))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("This account has been suspended. Contact support.", "danger")
            return render_template("auth/login.html")

        login_user(user)
        next_url = request.args.get("next")
        if next_url:
            return redirect(next_url)

        if user.role == Role.VENDOR and user.restaurant is None:
            return redirect(url_for("vendor.create_restaurant"))
        return redirect(url_for(_role_home(user.role)))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
