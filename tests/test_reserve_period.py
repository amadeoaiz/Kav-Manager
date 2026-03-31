"""Tests for src/domain/reserve_period — reserve period resolution."""
from datetime import datetime

from src.core.models import UnitConfig, DraftInterval, PresenceInterval, Soldier
from src.domain.reserve_period import resolve_reserve_period


def test_no_data_returns_none(db):
    start, end = resolve_reserve_period(db)
    assert start is None
    assert end is None


def test_manual_only(db):
    config = db.query(UnitConfig).first()
    config.reserve_period_start = datetime(2026, 3, 1)
    config.reserve_period_end = datetime(2026, 3, 28)
    db.commit()

    start, end = resolve_reserve_period(db)
    assert start == datetime(2026, 3, 1)
    assert end == datetime(2026, 3, 28)


def test_auto_detect_from_drafts(db):
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    db.add(DraftInterval(
        soldier_id=s.id,
        start_time=datetime(2026, 3, 5),
        end_time=datetime(2026, 3, 20),
        status="DRAFTED",
    ))
    db.commit()

    start, end = resolve_reserve_period(db)
    assert start == datetime(2026, 3, 5)
    assert end == datetime(2026, 3, 20)


def test_broader_of_manual_and_auto(db):
    """Manual range is narrower; auto-detect extends it."""
    config = db.query(UnitConfig).first()
    config.reserve_period_start = datetime(2026, 3, 5)
    config.reserve_period_end = datetime(2026, 3, 25)
    db.commit()

    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()
    db.add(DraftInterval(
        soldier_id=s.id,
        start_time=datetime(2026, 3, 1),
        end_time=datetime(2026, 3, 28),
        status="DRAFTED",
    ))
    db.commit()

    start, end = resolve_reserve_period(db)
    assert start == datetime(2026, 3, 1)   # earlier of the two starts
    assert end == datetime(2026, 3, 28)     # later of the two ends


def test_fallback_to_presence(db):
    """No drafts — falls back to PRESENT intervals."""
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()
    db.add(PresenceInterval(
        soldier_id=s.id,
        start_time=datetime(2026, 3, 3),
        end_time=datetime(2026, 3, 15),
        status="PRESENT",
    ))
    db.commit()

    start, end = resolve_reserve_period(db)
    assert start == datetime(2026, 3, 3)
    assert end == datetime(2026, 3, 15)
