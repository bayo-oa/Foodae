import click

from app.extensions import db
from app.models import User, Role


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(email, full_name, password):
        """Create the first admin user. Admins can never self-register via the website."""
        existing = User.query.filter_by(email=email).first()
        if existing:
            click.echo(f"A user with email {email} already exists (role={existing.role}).")
            return
        admin = User(email=email, full_name=full_name, role=Role.ADMIN, is_active=True)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Admin user created: {email}")
