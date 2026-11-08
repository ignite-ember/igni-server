"""What a deployment check found, and the exception that refuses to serve.

Shared by the settings checks (`app/security/startup_checks.py`) and the
database checks (`app/security/database_checks.py`), so both word a
refusal the same way and neither imports the other.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UnsafeDeployment(RuntimeError):
    """Raised at startup when a setting would leave the door open."""


class DeploymentFindings(BaseModel):
    """Every problem one pass of checks found, split by whether it is fatal.

    A *fatal* finding is one the server must not serve through outside
    dev; a *warning* is logged and shown to super admins, and the
    deployment carries on.
    """

    fatal: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @staticmethod
    def explain(problem: str, fix: str) -> str:
        """A finding as an operator reads it: what is wrong, then what to do."""
        return f'{problem}\n  Fix: {fix}'

    def refuse(self, problem: str, fix: str) -> None:
        """Record a finding the server must not start with."""
        self.fatal.append(self.explain(problem, fix))

    def warn(self, message: str) -> None:
        """Record a finding the server starts with and says out loud."""
        self.warnings.append(message)

    def all(self) -> list[str]:
        """Warnings first, then the fatal findings."""
        return self.warnings + self.fatal
