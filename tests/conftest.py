import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.models import Base, UnitConfig, Role
from src.core.database import _SEED_ROLES


@pytest.fixture()
def db():
    """In-memory SQLite database, fully seeded with roles and config."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed roles (same as production init_db)
    existing = {}
    for name, description, _ in _SEED_ROLES:
        role = Role(name=name, description=description)
        session.add(role)
        existing[name] = role
    session.flush()
    for name, _, parent_name in _SEED_ROLES:
        if parent_name and parent_name in existing:
            existing[name].parent_role_id = existing[parent_name].id
    session.flush()

    # Seed default unit config
    session.add(UnitConfig())
    session.commit()

    yield session
    session.close()
