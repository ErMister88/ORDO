"""Migration definitions and framework-specific exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Protocol


class DatabaseLike(Protocol):
    """The small PyMongo database surface migrations are allowed to use."""

    name: str


@dataclass(frozen=True)
class MigrationPlan:
    preconditions: tuple[str, ...] = ()
    expected_changes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MigrationContext:
    application_version: str
    database_name: str
    attempt: int
    _lease_checkpoint: Callable[[], None] = field(repr=False)

    def checkpoint(self) -> None:
        """Fail if this runner no longer owns the migration lease."""

        self._lease_checkpoint()


InspectMigration = Callable[[DatabaseLike], MigrationPlan]
ApplyMigration = Callable[[DatabaseLike, MigrationContext], Mapping[str, Any]]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    inspect: InspectMigration
    apply: ApplyMigration
    depends_on: tuple[int, ...] = ()

    def validate(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise MigrationDefinitionError("Migration versions must be positive integers")
        if not self.name or not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise MigrationDefinitionError(
                f"Migration {self.version} has an invalid name; use lowercase snake_case"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum):
            raise MigrationDefinitionError(
                f"Migration {self.version} has an invalid SHA-256 checksum"
            )
        if any(type(version) is not int or version < 1 for version in self.depends_on):
            raise MigrationDefinitionError(
                f"Migration {self.version} has invalid dependency versions"
            )
        if self.version in self.depends_on:
            raise MigrationDefinitionError(
                f"Migration {self.version} cannot depend on itself"
            )


def checksum_file(path: str | Path) -> str:
    """Return the checksum of the complete, immutable migration source file."""

    source = Path(path).read_bytes()
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return sha256(normalized).hexdigest()


class MigrationError(RuntimeError):
    """Base class for expected migration failures."""


class MigrationDefinitionError(MigrationError):
    pass


class MigrationStateError(MigrationError):
    pass


class ChecksumMismatchError(MigrationStateError):
    pass


class ProductionGuardError(MigrationError):
    pass


class MigrationLockUnavailable(MigrationError):
    pass


class MigrationLockLost(MigrationError):
    pass


class DryRunWriteError(MigrationError):
    pass


_URI_CREDENTIALS = re.compile(r"(?P<scheme>mongodb(?:\+srv)?://)[^\s/@]+@", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+"
)


def safe_error_message(exc: BaseException, limit: int = 500) -> str:
    """Keep operational context while removing common credential-bearing URI forms."""

    message = str(exc).replace("\n", " ").replace("\r", " ")
    message = _URI_CREDENTIALS.sub(r"\g<scheme><redacted>@", message)
    message = _SENSITIVE_ASSIGNMENT.sub(r"\1=<redacted>", message)
    return message[:limit] or exc.__class__.__name__
