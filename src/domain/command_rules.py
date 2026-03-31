"""Chain of command resolution logic.

Pure functions — no DB access, no side effects.
"""
from datetime import datetime


def resolve_active_commander(
    command_chain: list[int],
    soldier_presence: dict[int, list[tuple[datetime, datetime]]],
    at_time: datetime,
) -> int | None:
    """Return the soldier_id of the active commander at `at_time`, or None.

    Walks the chain in order (primary → secondary → tertiary).  Returns the
    first soldier who is present at `at_time` according to their presence
    intervals.  Returns None if the chain is empty or no one is present.

    Args:
        command_chain: Ordered soldier IDs [primary, secondary, tertiary].
        soldier_presence: soldier_id → list of (start, end) presence intervals.
        at_time: The moment to resolve.
    """
    for sid in command_chain:
        intervals = soldier_presence.get(sid, [])
        for start, end in intervals:
            if start <= at_time < end:
                return sid
    return None
