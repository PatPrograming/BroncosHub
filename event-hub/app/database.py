"""Initialize app tables without exposing student data through a shared API."""

import re

from sqlalchemy import text

from app.extensions import db


def initialize_database(app):
    if db.engine.dialect.name != "postgresql":
        db.create_all()
        return

    schema = app.config.get("DATABASE_SCHEMA", "broncoshub")
    if (not isinstance(schema, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema)
            or schema in {"public", "information_schema"} or schema.startswith("pg_")):
        raise RuntimeError("PostgreSQL needs a dedicated private DATABASE_SCHEMA.")

    # Provision the schema separately. Failing here prevents an incorrectly
    # configured connection from silently creating application tables in public.
    with db.engine.begin() as connection:
        if connection.scalar(text("SELECT current_schema()")) != schema:
            raise RuntimeError("Provision DATABASE_SCHEMA and use it as the database search_path.")
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
            {"name": f"broncoshub-schema:{schema}"},
        )
        db.metadata.create_all(bind=connection)
        quote = db.engine.dialect.identifier_preparer.quote
        schema_sql = quote(schema)
        roles = ["PUBLIC", *connection.scalars(text(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
        ))]
        roles_sql = ", ".join("PUBLIC" if role == "PUBLIC" else quote(role) for role in roles)
        for table in db.metadata.sorted_tables:
            connection.execute(text(
                f"ALTER TABLE {schema_sql}.{quote(table.name)} ENABLE ROW LEVEL SECURITY"
            ))
        # Flask authenticates and authorizes requests. The owner connection can
        # use its tables; Supabase's anonymous/client roles receive no access.
        connection.execute(text(f"REVOKE ALL ON SCHEMA {schema_sql} FROM {roles_sql}"))
        connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema_sql} FROM {roles_sql}"))
        connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema_sql} FROM {roles_sql}"))

