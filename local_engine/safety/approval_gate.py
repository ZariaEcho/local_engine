"""Explicit opt-in gate for apply mode."""


def require_approval(approved: bool) -> None:
    if not approved:
        raise PermissionError("apply mode requires explicit user approval")
