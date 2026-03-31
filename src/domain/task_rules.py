from src.core.models import Task


def task_roles_list(task: Task) -> list[str]:
    """
    Normalize task.required_roles_list to a list of role names.

    required_count = total soldiers needed.
    required_roles_list = specific role slots (dict {role: count}).
    Remaining slots (total - sum of roles) become "Soldier" wildcards.
    """
    rl = task.required_roles_list
    total = max(task.required_count or 1, 1)

    if isinstance(rl, dict) and rl:
        explicit = [r for r, c in rl.items() for _ in range(max(0, int(c)))]
    elif isinstance(rl, list) and rl:
        explicit = list(rl)
    else:
        explicit = []

    total = max(total, len(explicit))
    wildcards = total - len(explicit)
    return explicit + ["Soldier"] * wildcards


def format_task_roles_display(required_roles_list, required_count: int = 1) -> str:
    """
    Format task roles for UI display. Never shows "Soldier" (everyone is a soldier).
    Returns 'Any x N' when only generic soldiers are required.
    """
    total = max(required_count or 1, 1)

    if not required_roles_list:
        return f"Any x{total}" if total > 1 else "Any"

    if isinstance(required_roles_list, dict):
        parts = [
            f"{rn} x{cnt}" if cnt != 1 else rn
            for rn, cnt in required_roles_list.items()
            if rn != "Soldier"
        ]
        role_sum = sum(c for rn, c in required_roles_list.items() if rn != "Soldier")
    else:
        parts = [r for r in required_roles_list if r != "Soldier"]
        role_sum = len(parts)

    total = max(total, role_sum)
    wildcards = total - role_sum
    if wildcards > 0:
        parts.append(f"Any x{wildcards}" if wildcards > 1 else "Any")

    return ", ".join(parts) if parts else ("Any" if total == 1 else f"Any x{total}")


# Backward-compatible alias while modules migrate.
def _task_roles_list(task: Task) -> list[str]:
    return task_roles_list(task)
