"""Run completion payload returned to CLI and compatibility adapters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from local_engine.runtime.artifact_applier import DeliveryStatus, VerificationStatus


@dataclass
class RunOutcome:
    run_id: str
    report_dir: Path
    final_report: Path
    warnings: List[str]
    task_statuses: Dict[str, str]
    lifecycle_statuses: Dict[str, str] = None
    task_graph_status: str = ""
    delivery_status: str = DeliveryStatus.NOT_STARTED.value
    verification_status: str = VerificationStatus.NOT_RUN.value
    files_created: List[str] = None
    files_modified: List[str] = None
    files_not_applied: List[str] = None
    user_goal_satisfied: bool = False
    error_log: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.lifecycle_statuses is None:
            self.lifecycle_statuses = {}
        if self.files_created is None:
            self.files_created = []
        if self.files_modified is None:
            self.files_modified = []
        if self.files_not_applied is None:
            self.files_not_applied = []

    @property
    def has_failures(self) -> bool:
        failed_statuses = {"failed_but_continued", "failed", "needs_human", "logic_failed"}
        failed_delivery = self.delivery_status in {
            DeliveryStatus.FAILED.value,
            DeliveryStatus.NEEDS_HUMAN.value,
            DeliveryStatus.ARTIFACTS_GENERATED.value,
        } and bool(self.files_not_applied or not self.user_goal_satisfied)
        return (
            any(status in failed_statuses for status in self.task_statuses.values())
            or any(status in failed_statuses for status in self.lifecycle_statuses.values())
            or failed_delivery
        )
