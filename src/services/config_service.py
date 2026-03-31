from sqlalchemy.orm import Session

from src.core.models import UnitConfig, Role


class ConfigService:
    """
    Application service for unit configuration and role registry.
    Wraps all UnitConfig and Role DB access so the UI layer
    never imports or queries these models directly.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── UnitConfig ────────────────────────────────────────────────────────────

    def get_config(self) -> UnitConfig:
        """Return the single UnitConfig row, creating with defaults if missing."""
        config = self.db.query(UnitConfig).first()
        if not config:
            config = UnitConfig()
            self.db.add(config)
            self.db.flush()
        return config

    def save_config(self, **fields) -> UnitConfig:
        """Update UnitConfig with the given fields and commit."""
        config = self.get_config()
        for key, value in fields.items():
            setattr(config, key, value)
        self.db.commit()
        return config

    def get_unit_codename(self) -> str:
        config = self.get_config()
        return config.unit_codename or ''

    def get_night_window(self) -> tuple[int, int]:
        config = self.get_config()
        return (config.night_start_hour or 23, config.night_end_hour or 7)

    # ── Role CRUD ─────────────────────────────────────────────────────────────

    def current_theme(self) -> str:
        """Return the current UI theme name."""
        config = self.db.query(UnitConfig).first()
        return config.theme if config else "dark"

    # ── Role CRUD ─────────────────────────────────────────────────────────────

    def list_roles(self) -> list[Role]:
        return self.db.query(Role).order_by(Role.name).all()

    def list_roles_for_picker(self) -> list[Role]:
        """Roles excluding the implicit 'Soldier' wildcard, for UI pickers."""
        return (
            self.db.query(Role)
            .filter(Role.name != "Soldier")
            .order_by(Role.name)
            .all()
        )

    def get_role(self, role_id: int) -> Role | None:
        return self.db.query(Role).filter(Role.id == role_id).first()

    def get_role_by_name(self, name: str) -> Role | None:
        return self.db.query(Role).filter(Role.name == name).first()

    def create_role(self, name: str, description: str = None,
                    parent_role_id: int = None) -> Role:
        role = Role(name=name, description=description,
                    parent_role_id=parent_role_id)
        self.db.add(role)
        self.db.commit()
        return role

    def update_role(self, role_id: int, **fields) -> Role | None:
        role = self.db.query(Role).filter(Role.id == role_id).first()
        if not role:
            return None
        for key, value in fields.items():
            setattr(role, key, value)
        self.db.commit()
        return role

    def delete_role(self, role_id: int) -> bool:
        """Delete a role and detach its children. Returns False if not found."""
        role = self.db.query(Role).filter(Role.id == role_id).first()
        if not role:
            return False
        for child in self.db.query(Role).filter(
            Role.parent_role_id == role_id
        ).all():
            child.parent_role_id = None
        self.db.delete(role)
        self.db.commit()
        return True
