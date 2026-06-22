"""Best-effort dynamic patch and worker-output checks."""

import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from local_engine.kernel.schemas import TaskResult


_PATCH_TARGET = re.compile(r"^\+\+\+\s+(?:b/)?(.+)$", re.MULTILINE)


def check_conflicts(results: Dict[str, TaskResult], patches: Iterable[Path]) -> List[str]:
    warnings: List[str] = []
    for task_id, result in results.items():
        result_type = str(result.sip.get("type", "unstructured"))
        if result_type in {"unstructured", "parse_error", "error"} or result.status == "failed_but_continued":
            warnings.append("{0} returned {1}; its raw output was preserved for review.".format(task_id, result_type))

    targets = defaultdict(list)
    for patch in patches:
        try:
            body = patch.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append("Could not inspect patch {0}: {1}".format(patch.name, exc))
            continue
        for target in _PATCH_TARGET.findall(body):
            targets[target].append(patch.stem)
    for target, task_ids in targets.items():
        if len(task_ids) > 1:
            warnings.append("Multiple task patches modify `{0}`: {1}.".format(target, ", ".join(sorted(task_ids))))
    return warnings
