import os

from flask import Flask, render_template

from config import get_config
from app.extensions import db, login_manager, migrate, limiter


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    # Make sure the local upload folder exists (no-op in prod if you've
    # swapped save_upload() for Cloudinary per the README's known gaps).
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # --- extensions -------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, user_id)

    # --- blueprints ---------------------------------------------------
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.customer.routes import customer_bp
    from app.blueprints.vendor.routes import vendor_bp
    from app.blueprints.rider.routes import rider_bp
    from app.blueprints.admin.routes import admin_bp

    app.register_blueprint(auth_bp)              # already has url_prefix="/auth"
    app.register_blueprint(customer_bp)           # root storefront: "/", "/cart", "/checkout", ...
    app.register_blueprint(vendor_bp, url_prefix="/vendor")
    app.register_blueprint(rider_bp, url_prefix="/rider")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # --- CLI commands (flask create-admin) --------------------------------
    from app.cli import register_cli
    register_cli(app)

    # --- error handlers -----------------------------------------------
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors.html", code=403,
                                message="You don't have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors.html", code=404,
                                message="We couldn't find what you were looking for."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("errors.html", code=413,
                                message="That file is too large. Max upload size is 5MB."), 413

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("errors.html", code=500,
                                message="Something went wrong on our end. Please try again."), 500

    return app