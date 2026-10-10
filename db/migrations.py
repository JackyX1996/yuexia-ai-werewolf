"""Small, idempotent schema upgrades for existing installations."""
from sqlalchemy import Boolean, Column, false, inspect, text
from sqlalchemy.schema import CreateColumn


def ensure_user_admin_role(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("user")}
    if "is_admin" in columns:
        return

    table = connection.dialect.identifier_preparer.quote("user")
    column = Column("is_admin", Boolean, nullable=False, server_default=false())
    definition = str(CreateColumn(column).compile(dialect=connection.dialect))
    connection.exec_driver_sql("ALTER TABLE {0} ADD COLUMN {1}".format(table, definition))
    # Only the configured human administrator inherits the old permission.
    # Another account is not promoted just because its username is admin.
    from config import settings
    connection.execute(text(
        "UPDATE {0} SET is_admin = :admin WHERE username = :legacy AND is_ai = :ai".format(table)
    ), {"admin": True, "legacy": settings.admin_username, "ai": False})
