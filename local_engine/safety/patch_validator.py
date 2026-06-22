"""Conservative validation for patches before any apply-mode mutation."""

import re
from typing import Iterable

from local_engine.safety.permission_guard import is_safe_project_relative


FORBIDDEN_TEXT = ("rm -rf", "git push", "drop database", "drop table", "truncate table")
_PATCH_PATH = re.compile(r"^(?:\+\+\+|---)\s+(?:a/|b/)?(.+)$", re.MULTILINE)


def validate_patch_text(patch: str) -> None:
    lowered = patch.lower()
    if any(token in lowered for token in FORBIDDEN_TEXT):
        raise ValueError("patch contains a forbidden destructive command")
    if re.search(r"^deleted file mode", patch, re.MULTILINE):
        raise ValueError("file deletion is not allowed")
    for raw_path in _PATCH_PATH.findall(patch):
        path = raw_path.strip()
        if path == "/dev/null":
            raise ValueError("file deletion is not allowed")
        if not is_safe_project_relative(path):
            raise ValueError("patch targets an unsafe path: {0}".format(path))


def validate_patch_set(patches: Iterable[str]) -> None:
    for patch in patches:
        validate_patch_text(patch)
