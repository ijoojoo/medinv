import os

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

if context.is_offline_mode():
    raise RuntimeError("Use an online migration against a reviewed target database")
engine = create_engine(os.environ["MED_MIGRATION_URL"], poolclass=NullPool, hide_parameters=True)
with engine.begin() as connection:
    connection.execute(text("SET ROLE med_owner"))
    context.configure(connection=connection, target_metadata=None, version_table_schema="med")
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
