"""
Tests for src/domain/presence_calc — presence fraction and weighted average utilities.
"""
from datetime import datetime, date, timedelta

from src.core.models import Soldier
from src.domain.presence_rules import insert_presence_interval
from src.domain.presence_calc import (
    compute_domain_fractions,
    count_present_partial,
    weighted_avg_sd,
    domain_window_hours,
)


def test_domain_window_hours():
    day_h, night_h = domain_window_hours(23, 7)
    assert day_h == 16.0
    assert night_h == 8.0


def test_full_presence_gives_fraction_one(db):
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    # Present for the full day
    insert_presence_interval(
        db, s.id, datetime(2026, 3, 5, 0, 0), datetime(2026, 3, 6, 7, 0), "PRESENT",
    )
    db.flush()

    day_f, night_f = compute_domain_fractions(db, [s.id], date(2026, 3, 5), 23, 7)
    assert abs(day_f[s.id] - 1.0) < 0.01
    assert abs(night_f[s.id] - 1.0) < 0.01


def test_half_day_presence(db):
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    # Present 07:00-15:00 = 8h of the 16h day domain (07:00-23:00)
    insert_presence_interval(
        db, s.id, datetime(2026, 3, 5, 7, 0), datetime(2026, 3, 5, 15, 0), "PRESENT",
    )
    db.flush()

    day_f, night_f = compute_domain_fractions(db, [s.id], date(2026, 3, 5), 23, 7)
    assert abs(day_f[s.id] - 0.5) < 0.01
    assert abs(night_f[s.id]) < 0.01  # no night presence


def test_absent_soldier_fraction_zero(db):
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    day_f, night_f = compute_domain_fractions(db, [s.id], date(2026, 3, 5), 23, 7)
    assert day_f[s.id] == 0.0
    assert night_f[s.id] == 0.0


def test_count_present_partial(db):
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="001", role=[], is_active_in_kav=True)
    s3 = Soldier(name="Charlie", phone_number="002", role=[], is_active_in_kav=True)
    db.add_all([s1, s2, s3])
    db.flush()

    # s1 fully present
    insert_presence_interval(
        db, s1.id, datetime(2026, 3, 5, 0, 0), datetime(2026, 3, 6, 0, 0), "PRESENT",
    )
    # s2 partially present (morning only)
    insert_presence_interval(
        db, s2.id, datetime(2026, 3, 5, 8, 0), datetime(2026, 3, 5, 16, 0), "PRESENT",
    )
    # s3 absent (no intervals)
    db.flush()

    full, partial = count_present_partial(
        db, [s1.id, s2.id, s3.id], date(2026, 3, 5),
    )
    assert full == 1
    assert partial == 1


def test_weighted_avg_sd_basic():
    hours = {1: 4.0, 2: 2.0}
    fracs = {1: 1.0, 2: 0.5}

    # weighted avg = (4+2) / (1.0+0.5) = 6/1.5 = 4.0
    # rates: s1 = 4/1 = 4, s2 = 2/0.5 = 4 => both same => sd = 0
    avg, sd = weighted_avg_sd(hours, fracs)
    assert abs(avg - 4.0) < 0.01
    assert abs(sd) < 0.01


def test_weighted_avg_excludes_zero_frac():
    hours = {1: 4.0, 2: 2.0, 3: 10.0}
    fracs = {1: 1.0, 2: 0.5, 3: 0.0}  # s3 absent

    avg, sd = weighted_avg_sd(hours, fracs)
    # Only s1 and s2 count: avg = 6/1.5 = 4.0
    assert abs(avg - 4.0) < 0.01
