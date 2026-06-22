"""Small deterministic classifier for common repository work intents."""

from typing import Dict, Iterable


AUDIT = "AUDIT"
PLAN = "PLAN"
LEARN = "LEARN"
BUILD = "BUILD"
TEST = "TEST"
DOCUMENT = "DOCUMENT"
REFACTOR = "REFACTOR"
RESEARCH = "RESEARCH"


class IntentClassifier:
    """Classify text without making an external model call."""

    _KEYWORDS: Dict[str, Iterable[str]] = {
        AUDIT: ("审计", "审核", "audit", "inspect project", "code review"),
        LEARN: ("知识点", "知识缺口", "学习", "learn", "knowledge gap"),
        REFACTOR: ("重构", "refactor", "整理代码"),
        TEST: ("测试", "test", "coverage", "验证"),
        DOCUMENT: ("文档", "documentation", "document", "readme"),
        RESEARCH: ("调研", "研究", "research", "compare"),
        BUILD: ("实现", "构建", "新增功能", "开发", "build", "implement", "feature", "fix bug"),
        PLAN: ("计划", "方案", "如何做", "plan", "roadmap", "设计"),
    }

    def classify(self, user_input: str) -> str:
        text = (user_input or "").lower()
        for intent in (AUDIT, LEARN, REFACTOR, TEST, DOCUMENT, RESEARCH, BUILD, PLAN):
            if any(keyword in text for keyword in self._KEYWORDS[intent]):
                return intent
        return PLAN


def classify(user_input: str) -> str:
    """Convenience API for ``IntentClassifier().classify(user_input)``."""
    return IntentClassifier().classify(user_input)
