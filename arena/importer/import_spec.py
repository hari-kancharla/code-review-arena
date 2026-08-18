"""Strict import specification for historical-fix ingestion.

The spec carries only the semantic information that cannot be derived safely from
Git (title, category, severity, description, ground truth, execution/validation
config, and which committed paths are source vs tests). It reuses the existing
strict case models, so importer validation cannot drift from normal pack
validation, and it never infers semantic fields with heuristics or a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from arena.core import limits
from arena.core.bounded_io import read_yaml_mapping_bounded
from arena.core.errors import ImportFixError
from arena.core.models import (
    BenchmarkCase,
    BoundedName,
    CaseInput,
    ExecutionConfig,
    GroundTruth,
    IsoDate,
    MetricsConfig,
    ScoringConfig,
    Severity,
    ValidationConfig,
    _iso_date_before,
    _StrictExternal,
)
from arena.security.paths import SafeCaseId, SafeDirPath, SafeFilePath

_Version = Annotated[str, StringConstraints(min_length=1, max_length=limits.IDENTIFIER_LEN)]
_Title = Annotated[str, StringConstraints(min_length=1, max_length=limits.TITLE_LEN)]
_Category = Annotated[str, StringConstraints(min_length=1, max_length=limits.CATEGORY_LEN)]
_Description = Annotated[str, StringConstraints(max_length=limits.DESCRIPTION_LEN)]


class _ImportPack(_StrictExternal):
    version: _Version
    name: _Title


class _ImportCase(_StrictExternal):
    id: SafeCaseId
    title: _Title
    category: _Category
    severity: Severity
    stack: list[BoundedName] = Field(min_length=1, max_length=limits.STACK_ENTRIES)
    description: _Description


class _ImportOrigin(_StrictExternal):
    """Optional exposure facts a human knows that Git cannot supply.

    The importer derives the fix date from the commit itself. This block exists
    for the case where the defect became public EARLIER than the fix landed --
    an issue report or a security advisory that named it. A declared date later
    than the commit's own is ignored in favour of the commit's, because that is
    the direction that would flatter the case.
    """

    public_fix_date: IsoDate | None = None
    upstream_ref: Annotated[str, StringConstraints(max_length=limits.UPSTREAM_REF_LEN)] | None = (
        None
    )

    _normalize_date = field_validator("public_fix_date", mode="before")(_iso_date_before)


class ImportSpec(_StrictExternal):
    """The complete, human-authored semantic specification for one imported case."""

    schema_version: Literal["1"]
    pack: _ImportPack
    case: _ImportCase
    source_paths: list[SafeFilePath] = Field(min_length=1, max_length=limits.IMPORT_SOURCE_PATHS)
    tests_root: SafeDirPath | None = None
    ground_truth: GroundTruth
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    # Purely additive and optional, so every already-written spec still loads
    # against schema_version "1".
    origin: _ImportOrigin | None = None

    def to_case(self) -> BenchmarkCase:
        """Build the BenchmarkCase exactly as a normal pack would declare it."""
        return BenchmarkCase(
            id=self.case.id,
            title=self.case.title,
            category=self.case.category,
            severity=self.case.severity,
            stack=list(self.case.stack),
            description=self.case.description,
            input=CaseInput(tests_dir="tests" if self.tests_root else None),
            ground_truth=self.ground_truth,
            scoring=self.scoring,
            execution=self.execution,
            validation=self.validation,
            metrics=self.metrics,
        )


def load_import_spec(path: Path) -> ImportSpec:
    """Bounded, strict load of an import-spec YAML document."""
    from pydantic import ValidationError as PydanticValidationError

    data = read_yaml_mapping_bounded(path, limits.CASE_YAML_BYTES, label="import-spec.yaml")
    try:
        return ImportSpec.model_validate(data)
    except PydanticValidationError as exc:
        raise ImportFixError("invalid_spec", f"import spec is invalid: {exc}") from exc
