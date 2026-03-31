"""
Tests for TemplateService — CRUD, validation, crosses-midnight logic,
create-from-task, and duplicate.
"""
from datetime import datetime

import pytest

from src.core.database import engine, SessionLocal
from src.core.models import Base, Task, TaskTemplate
from src.services.template_service import TemplateService


@pytest.fixture(autouse=True)
def db():
    """Create a fresh in-memory-style session using the real engine with rollback."""
    connection = engine.connect()
    transaction = connection.begin()
    Base.metadata.create_all(bind=connection)
    session = SessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestCRUD:
    def test_create_and_list(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template(
            name="Night Guard",
            start_time_of_day="23:00",
            end_time_of_day="07:00",
            is_fractionable=True,
            required_count=2,
            required_roles_list={"Driver": 1},
            hardness=4,
        )
        assert tpl.id is not None
        assert tpl.name == "Night Guard"
        assert tpl.crosses_midnight is True

        templates = svc.list_templates()
        assert len(templates) == 1
        assert templates[0].name == "Night Guard"

    def test_get_template(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Test", "08:00", "16:00")
        fetched = svc.get_template(tpl.id)
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_nonexistent(self, db):
        svc = TemplateService(db)
        assert svc.get_template(9999) is None

    def test_update_template(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Old Name", "08:00", "16:00", hardness=3)
        updated = svc.update_template(tpl.id, name="New Name", hardness=5)
        assert updated.name == "New Name"
        assert updated.hardness == 5

    def test_update_nonexistent(self, db):
        svc = TemplateService(db)
        assert svc.update_template(9999, name="X") is None

    def test_delete_template(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("To Delete", "08:00", "16:00")
        assert svc.delete_template(tpl.id) is True
        assert svc.get_template(tpl.id) is None

    def test_delete_nonexistent(self, db):
        svc = TemplateService(db)
        assert svc.delete_template(9999) is False

    def test_list_ordered_by_name(self, db):
        svc = TemplateService(db)
        svc.create_template("Zebra", "08:00", "16:00")
        svc.create_template("Alpha", "08:00", "16:00")
        svc.create_template("Middle", "08:00", "16:00")
        names = [t.name for t in svc.list_templates()]
        assert names == ["Alpha", "Middle", "Zebra"]


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:
    def test_empty_name(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="name"):
            svc.create_template("", "08:00", "16:00")

    def test_whitespace_name(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="name"):
            svc.create_template("   ", "08:00", "16:00")

    def test_invalid_start_time_format(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="start_time"):
            svc.create_template("Test", "8:00", "16:00")

    def test_invalid_end_time_not_15min(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="end_time"):
            svc.create_template("Test", "08:00", "16:10")

    def test_invalid_time_25_hour(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="start_time"):
            svc.create_template("Test", "25:00", "16:00")

    def test_hardness_too_low(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="hardness"):
            svc.create_template("Test", "08:00", "16:00", hardness=0)

    def test_hardness_too_high(self, db):
        svc = TemplateService(db)
        with pytest.raises(ValueError, match="hardness"):
            svc.create_template("Test", "08:00", "16:00", hardness=6)

    def test_valid_15min_boundaries(self, db):
        svc = TemplateService(db)
        for minutes in ["00", "15", "30", "45"]:
            tpl = svc.create_template(f"Test {minutes}", f"08:{minutes}", f"16:{minutes}")
            assert tpl.id is not None

    def test_update_validates(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Valid", "08:00", "16:00")
        with pytest.raises(ValueError, match="hardness"):
            svc.update_template(tpl.id, hardness=10)


# ── Crosses midnight ─────────────────────────────────────────────────────────

class TestCrossesMidnight:
    def test_crosses_midnight_true(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Night", "23:00", "07:00")
        assert tpl.crosses_midnight is True

    def test_crosses_midnight_false(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Day", "08:00", "16:00")
        assert tpl.crosses_midnight is False

    def test_same_time_crosses(self, db):
        """When end == start, crosses_midnight is True (e.g., 24-hour shift)."""
        svc = TemplateService(db)
        tpl = svc.create_template("Full Day", "08:00", "08:00")
        assert tpl.crosses_midnight is True

    def test_end_just_before_start(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Almost Full", "08:15", "08:00")
        assert tpl.crosses_midnight is True

    def test_update_recomputes_crosses(self, db):
        svc = TemplateService(db)
        tpl = svc.create_template("Day", "08:00", "16:00")
        assert tpl.crosses_midnight is False
        updated = svc.update_template(tpl.id, start_time_of_day="23:00", end_time_of_day="07:00")
        assert updated.crosses_midnight is True


# ── Create from task ─────────────────────────────────────────────────────────

class TestCreateFromTask:
    def test_basic(self, db):
        svc = TemplateService(db)
        task = Task(
            real_title="Night Guard",
            start_time=datetime(2026, 3, 29, 23, 0),
            end_time=datetime(2026, 3, 30, 7, 0),
            is_fractionable=True,
            required_count=2,
            required_roles_list={"Driver": 1},
            hardness=4,
        )
        tpl = svc.create_template_from_task(task)
        assert tpl.name == "Night Guard"
        assert tpl.start_time_of_day == "23:00"
        assert tpl.end_time_of_day == "07:00"
        assert tpl.crosses_midnight is True
        assert tpl.required_count == 2
        assert tpl.hardness == 4
        assert tpl.is_fractionable is True

    def test_day_task(self, db):
        svc = TemplateService(db)
        task = Task(
            real_title="Day Patrol",
            start_time=datetime(2026, 3, 29, 8, 0),
            end_time=datetime(2026, 3, 29, 16, 0),
            is_fractionable=False,
            required_count=3,
            required_roles_list=[],
            hardness=2,
        )
        tpl = svc.create_template_from_task(task)
        assert tpl.crosses_midnight is False
        assert tpl.is_fractionable is False
        assert tpl.hardness == 2


# ── Duplicate ────────────────────────────────────────────────────────────────

class TestDuplicate:
    def test_duplicate(self, db):
        svc = TemplateService(db)
        orig = svc.create_template(
            "Night Guard", "23:00", "07:00",
            is_fractionable=True, required_count=2, hardness=4,
        )
        dup = svc.duplicate_template(orig.id)
        assert dup is not None
        assert dup.id != orig.id
        assert dup.name == "Copy of Night Guard"
        assert dup.start_time_of_day == "23:00"
        assert dup.end_time_of_day == "07:00"
        assert dup.crosses_midnight is True
        assert dup.required_count == 2
        assert dup.hardness == 4

    def test_duplicate_nonexistent(self, db):
        svc = TemplateService(db)
        assert svc.duplicate_template(9999) is None
