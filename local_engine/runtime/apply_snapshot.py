"""File snapshots that let a failed apply restore the project to its prior state."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


class ApplySnapshot:
    """Record original bytes of target files and the directories an apply may create."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._originals: Dict[str, Optional[bytes]] = {}
        self._missing_dirs: Set[Path] = set()

    def capture(self, relative_paths: Iterable[str]) -> None:
        for relative in relative_paths:
            if relative in self._originals:
                continue
            target = self.project_root / relative
            self._originals[relative] = target.read_bytes() if target.is_file() else None
            parent = target.parent
            while parent != self.project_root and not parent.exists():
                self._missing_dirs.add(parent)
                parent = parent.parent

    def restore(self) -> List[str]:
        """Restore every captured file; return relative paths that could not be restored."""
        failed: List[str] = []
        for relative, original in self._originals.items():
            target = self.project_root / relative
            try:
                if original is None:
                    if target.is_file() or target.is_symlink():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original)
            except OSError:
                failed.append(relative)
        for directory in sorted(self._missing_dirs, key=lambda path: len(path.parts), reverse=True):
            with contextlib.suppress(OSError):
                directory.rmdir()
        return failed
