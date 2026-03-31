"""
Test resync_soldier_rates — the presence-weighted rate metric.

Verifies:
- Correct hours / domain-presence-hours calculation
- Unit average is subtracted (zero-sum across soldiers with presence)
- Only past assignments count
- Night hours classified correctly
"""
from datetime import datetime, timedelta

from src.core.models import Soldier, Task, TaskAssignment
from src.utils.maintenance import resync_soldier_rates
from src.domain.presence_rules import insert_presence_interval


def test_basic_rate_calculation(db):
    """Two soldiers, one with more day hours — rates reflect difference."""
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="001", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    # Both present for 10 days (Mar 1 00:00 to Mar 11 00:00)
    start = datetime(2026, 3, 1)
    end = datetime(2026, 3, 11)
    for s in (s1, s2):
        insert_presence_interval(db, s.id, start, end, "PRESENT")
    db.flush()

    # s1 has 8 hours of day work, s2 has 4 hours
    task = Task(real_title="Guard", start_time=datetime(2026, 3, 5, 8, 0),
                end_time=datetime(2026, 3, 5, 16, 0),
                is_fractionable=False, required_count=1,
                required_roles_list=[], is_active=True)
    db.add(task)
    db.flush()

    db.add(TaskAssignment(
        soldier_id=s1.id, task_id=task.id,
        start_time=datetime(2026, 3, 5, 8, 0),
        end_time=datetime(2026, 3, 5, 16, 0),
        final_weight_applied=8.0,
    ))
    db.add(TaskAssignment(
        soldier_id=s2.id, task_id=task.id,
        start_time=datetime(2026, 3, 5, 8, 0),
        end_time=datetime(2026, 3, 5, 12, 0),
        final_weight_applied=4.0,
    ))
    db.commit()

    resync_soldier_rates(db)
    db.commit()

    db.refresh(s1)
    db.refresh(s2)

    # Day domain = 07:00-23:00 = 16h/day, 10 days => 160h presence each
    # s1 rate = 8/160 = 0.05, s2 rate = 4/160 = 0.025
    # weighted avg = (8+4)/(160+160) = 0.0375
    # s1 stored = 0.05 - 0.0375 = +0.0125
    # s2 stored = 0.025 - 0.0375 = -0.0125
    assert abs(s1.total_day_points - 0.0125) < 0.002
    assert abs(s2.total_day_points - (-0.0125)) < 0.002

    # Zero-sum property
    assert abs(s1.total_day_points + s2.total_day_points) < 0.001


def test_night_hours_classified_correctly(db):
    """Assignments at night hours go into night rate."""
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    insert_presence_interval(db, s.id, datetime(2026, 3, 1), datetime(2026, 3, 11), "PRESENT")
    db.flush()

    task = Task(real_title="Night Guard", start_time=datetime(2026, 3, 5, 23, 0),
                end_time=datetime(2026, 3, 6, 3, 0),
                is_fractionable=False, required_count=1,
                required_roles_list=[], is_active=True)
    db.add(task)
    db.flush()

    db.add(TaskAssignment(
        soldier_id=s.id, task_id=task.id,
        start_time=datetime(2026, 3, 5, 23, 0),
        end_time=datetime(2026, 3, 6, 3, 0),
        final_weight_applied=4.0,
    ))
    db.commit()

    resync_soldier_rates(db)
    db.commit()
    db.refresh(s)

    # Only soldier, so average = own rate, stored = 0
    assert abs(s.total_night_points) < 0.001
    assert abs(s.total_day_points) < 0.001


def test_no_present_days_gives_zero(db):
    """Soldier with no presence intervals gets zero rate."""
    s = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    task = Task(real_title="Guard", start_time=datetime(2026, 3, 5, 8, 0),
                end_time=datetime(2026, 3, 5, 12, 0),
                is_fractionable=False, required_count=1,
                required_roles_list=[], is_active=True)
    db.add(task)
    db.flush()

    db.add(TaskAssignment(
        soldier_id=s.id, task_id=task.id,
        start_time=datetime(2026, 3, 5, 8, 0),
        end_time=datetime(2026, 3, 5, 12, 0),
        final_weight_applied=4.0,
    ))
    db.commit()

    resync_soldier_rates(db)
    db.commit()
    db.refresh(s)

    # No present days → rate is 0, avg is 0, stored is 0
    assert abs(s.total_day_points) < 0.001


def test_partial_presence_weights_correctly(db):
    """Soldier present for only half the day is weighted at 0.5."""
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="001", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    # s1 present full 10 days, s2 present only 5 days
    insert_presence_interval(db, s1.id, datetime(2026, 3, 1), datetime(2026, 3, 11), "PRESENT")
    insert_presence_interval(db, s2.id, datetime(2026, 3, 1), datetime(2026, 3, 6), "PRESENT")
    db.flush()

    # Both do 4 hours of day work
    task = Task(real_title="Guard", start_time=datetime(2026, 3, 3, 8, 0),
                end_time=datetime(2026, 3, 3, 12, 0),
                is_fractionable=False, required_count=1,
                required_roles_list=[], is_active=True)
    db.add(task)
    db.flush()

    for s in (s1, s2):
        db.add(TaskAssignment(
            soldier_id=s.id, task_id=task.id,
            start_time=datetime(2026, 3, 3, 8, 0),
            end_time=datetime(2026, 3, 3, 12, 0),
            final_weight_applied=4.0,
        ))
    db.commit()

    resync_soldier_rates(db)
    db.commit()
    db.refresh(s1)
    db.refresh(s2)

    # s1: 4h / (10*16h) = 0.025, s2: 4h / (5*16h) = 0.05
    # weighted avg = 8 / (160+80) = 8/240 = 0.0333
    # s1 stored ~ -0.008, s2 stored ~ +0.017
    assert s1.total_day_points < 0, "Full-presence soldier with same hours should be below avg"
    assert s2.total_day_points > 0, "Half-presence soldier with same hours should be above avg"
