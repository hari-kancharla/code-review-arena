import json
import subprocess
import sys
from pathlib import Path

import pytest

from arena.reviewers.custom_command import expand_command_template


@pytest.mark.parametrize("quoted", [False, True])
def test_expand_command_template_preserves_placeholder_argument_boundaries(quoted):
    placeholder = '"{case_json}"' if quoted else "{case_json}"
    args = expand_command_template(
        f"python reviewer.py --case {placeholder} --workspace={{workspace}} {{case_id}}",
        case_json=Path("/tmp/reviewer's files/case.json"),
        diff_file=Path("/tmp/reviewer's files/pr.diff"),
        case_id="example",
        workspace=Path('/tmp/reviewer "files"/workspace'),
    )
    assert args == [
        "python",
        "reviewer.py",
        "--case",
        "/tmp/reviewer's files/case.json",
        '--workspace=/tmp/reviewer "files"/workspace',
        "example",
    ]

    # Exercise real argv delivery, not just our list representation. No shell.
    completed = subprocess.run(
        [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(completed.stdout) == args
