from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    # Tests pass their URL through attributes: the ini-file API would choke on the "%" of a
    # URL-encoded password.
    url = config.attributes.get("database_url") or get_settings().sqlalchemy_database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set. See docs/SUPABASE_SETUP.md.")
    return str(url)


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL for review, no database needed."""
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        database_url(), poolclass=pool.NullPool, connect_args={"prepare_threshold": None}
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
