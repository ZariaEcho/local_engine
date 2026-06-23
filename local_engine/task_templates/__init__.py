"""Declarative task-template definitions and discovery."""

from local_engine.task_templates.registry import TaskTemplateRegistry
from local_engine.task_templates.schema import TaskTemplateDefinition

__all__ = ["TaskTemplateDefinition", "TaskTemplateRegistry"]
