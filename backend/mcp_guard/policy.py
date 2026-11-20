"""Policy engine: decides allow / block / ask for a server + tool pair."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from .config import PolicyAction
from .models import PolicyRule


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    rule: PolicyRule | None  # None when the default action applied

    @property
    def reason(self) -> str:
        if self.rule is None:
            return f"default action: {self.action}"
        label = f"rule #{self.rule.id} ({self.rule.server}/{self.rule.tool} -> {self.action})"
        return f"{label}: {self.rule.note}" if self.rule.note else label


def evaluate(
    server: str, tool: str, rules: list[PolicyRule], default_action: PolicyAction
) -> PolicyDecision:
    """First matching rule wins. ``rules`` should be ordered by priority."""
    for rule in rules:
        if fnmatchcase(server, rule.server) and fnmatchcase(tool, rule.tool):
            return PolicyDecision(rule.action, rule)
    return PolicyDecision(default_action, None)
