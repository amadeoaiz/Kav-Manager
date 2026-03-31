from sqlalchemy.orm import Session

from src.core.models import GearItem, TeamGearItem


class GearService:
    """
    Application service for soldier gear and team gear (rashmatz) CRUD.
    Wraps all GearItem and TeamGearItem DB access so the UI layer
    and bot never query these models directly.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Soldier gear (GearItem) ────────────────────────────────────────────

    def list_soldier_gear(self, soldier_id: int) -> list[GearItem]:
        return (
            self.db.query(GearItem)
            .filter(GearItem.soldier_id == soldier_id)
            .order_by(GearItem.item_name)
            .all()
        )

    def add_soldier_gear(self, soldier_id: int, item_name: str,
                         quantity: int = 1,
                         serial_number: str = None) -> GearItem:
        item = GearItem(
            soldier_id=soldier_id,
            item_name=item_name,
            quantity=quantity,
            serial_number=serial_number,
        )
        self.db.add(item)
        self.db.commit()
        return item

    def update_soldier_gear(self, gear_id: int, **fields) -> GearItem | None:
        item = self.db.query(GearItem).filter(GearItem.id == gear_id).first()
        if not item:
            return None
        for key, value in fields.items():
            setattr(item, key, value)
        self.db.commit()
        return item

    def delete_soldier_gear(self, gear_id: int) -> bool:
        item = self.db.query(GearItem).filter(GearItem.id == gear_id).first()
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True

    # ── Team gear (TeamGearItem) ───────────────────────────────────────────

    def list_team_gear(self) -> list[TeamGearItem]:
        return (
            self.db.query(TeamGearItem)
            .order_by(TeamGearItem.item_name)
            .all()
        )

    def add_team_gear(self, item_name: str, quantity: int = 1,
                      serial_number: str = None,
                      notes: str = None) -> TeamGearItem:
        item = TeamGearItem(
            item_name=item_name,
            quantity=quantity,
            serial_number=serial_number,
            notes=notes,
        )
        self.db.add(item)
        self.db.commit()
        return item

    def update_team_gear(self, gear_id: int, **fields) -> TeamGearItem | None:
        item = (
            self.db.query(TeamGearItem)
            .filter(TeamGearItem.id == gear_id)
            .first()
        )
        if not item:
            return None
        for key, value in fields.items():
            setattr(item, key, value)
        self.db.commit()
        return item

    def delete_team_gear(self, gear_id: int) -> bool:
        item = (
            self.db.query(TeamGearItem)
            .filter(TeamGearItem.id == gear_id)
            .first()
        )
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True
