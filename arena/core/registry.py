"""Factory for reviewer command-line identifiers."""

from __future__ import annotations

import sys
from typing import Literal

from arena.core.errors import ReviewerError
from arena.reviewers.base import BaseReviewer
from arena.reviewers.controls import ControlReviewer
from arena.reviewers.custom_command import CustomCommandReviewer
from arena.reviewers.docker_command import DockerCommandReviewer
from arena.reviewers.http import HttpReviewer
from arena.reviewers.reference_patch import ReferencePatchReviewer
from arena.reviewers.shallow_patch import ShallowPatchReviewer


def create_reviewer(
    spec: str,
    *,
    command: str | None = None,
    model: str | None = None,
    reviewer_timeout_seconds: int = 120,
    reveal_metadata: bool = False,
    enable_repair: bool = False,
    reveal_test_output: bool = False,
    docker_image: str | None = None,
) -> BaseReviewer:
    if spec in {"reference-patch", "control:reference_patch"}:
        return ReferencePatchReviewer()
    if spec in {"shallow-patch", "control:shallow_patch"}:
        return ShallowPatchReviewer()
    if spec.startswith(("openai:", "http:")):
        style: Literal["openai", "json"] = "openai" if spec.startswith("openai:") else "json"
        url = spec.partition(":")[2]
        if not url:
            raise ReviewerError(
                "HTTP reviewer needs a URL, e.g. openai:http://localhost:11434/v1 "
                "or http:http://localhost:8080/review"
            )
        return HttpReviewer(
            url,
            style=style,
            model=model,
            timeout_seconds=reviewer_timeout_seconds,
            reveal_metadata=reveal_metadata,
            enable_repair=enable_repair,
            reveal_test_output=reveal_test_output,
        )
    if spec.startswith("control"):
        mode = spec.partition(":")[2] or "perfect"
        return ControlReviewer(mode)
    if spec.startswith("mock"):
        print(
            "DEPRECATED: the mock:* reviewer spec is renamed control:*; "
            "the alias will be removed in a future release.",
            file=sys.stderr,
        )
        mode = spec.partition(":")[2] or "perfect"
        return ControlReviewer(mode)
    if spec == "custom-command":
        if not command:
            raise ReviewerError("--command is required for the custom-command reviewer.")
        return CustomCommandReviewer(
            command,
            reviewer_timeout_seconds,
            reveal_metadata,
            enable_repair,
            reveal_test_output=reveal_test_output,
        )
    if spec == "docker-command":
        if not command:
            raise ReviewerError("--command is required for the docker-command reviewer.")
        if not docker_image:
            raise ReviewerError(
                "--docker-image is required for the docker-command reviewer: it must name "
                "an image you built or vetted (the container is never pulled)."
            )
        return DockerCommandReviewer(
            command,
            docker_image,
            reviewer_timeout_seconds,
            reveal_metadata,
            enable_repair,
            reveal_test_output=reveal_test_output,
        )
    raise ReviewerError(
        f"Unknown reviewer: {spec}. "
        "Available: control:<mode> (deprecated alias mock:<mode>), reference-patch, "
        "shallow-patch (generic adversarial baseline), custom-command, "
        "docker-command (containerized custom-command; needs --docker-image), "
        "openai:<base_url> (local OpenAI-compatible servers), "
        "http:<url> (plain JSON endpoint). Benchmark a real model via custom-command "
        "or a local openai:/http: endpoint."
    )
