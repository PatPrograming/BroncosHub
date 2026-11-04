"""Local administrator commands; role changes are never exposed as a web form."""

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import func, update

from app.extensions import db
from app.models import User


@click.command("user-role")
@click.argument("email")
@click.option("--role", required=True, type=click.Choice(("student", "organizer", "admin")),
              help="Set admin to grant moderation or student to revoke it. All SCU accounts can host events.")
@click.option("--yes", is_flag=True, help="Explicitly confirm this role change without a prompt.")
@with_appcontext
def user_role(email, role, yes):
    """Assign an explicit role to an existing SCU Google account.

    Example: flask --app run user-role moderator@scu.edu --role admin
    The account must first have signed in through Google. This command cannot
    create accounts, set passwords, or bypass Google sign-in. The legacy
    organizer role is retained; students and organizers can both host events.
    """
    email = email.strip().lower()
    local, separator, domain = email.partition("@")
    allowed = current_app.config["ALLOWED_EMAIL_DOMAIN"].strip().lower()
    if (not local or separator != "@" or domain != allowed
            or len(email) > 255 or any(char.isspace() for char in email)):
        raise click.ClickException("Use an existing account in the configured SCU email domain.")
    account = db.session.scalar(db.select(User).where(func.lower(User.email) == email))
    if account is None or not isinstance(account.google_sub, str) or not account.google_sub.strip():
        raise click.ClickException("No Google-linked account found. That person must sign in first.")

    account_id, previous_role = account.id, account.role
    db.session.rollback()
    if previous_role == role:
        click.echo(f"{email} already has the {role} role. No change made.")
        return
    click.echo(f"Change {email}: {previous_role} -> {role}")
    if not yes:
        click.confirm("Apply this account permission change?", abort=True)

    # Someone else may change the role while the confirmation prompt is open.
    result = db.session.execute(update(User).where(
        User.id == account_id, User.role == previous_role,
    ).values(role=role))
    if result.rowcount != 1:
        db.session.rollback()
        raise click.ClickException("The account changed while waiting. Review it and try again.")
    db.session.commit()
    click.echo(f"Saved. {email} now has the {role} role.")


def register_commands(app):
    app.cli.add_command(user_role)
