from pathlib import Path

import pytest

from arena.reviewers.custom_command import expand_command_template


def test_expand_command_template_preserves_literal_values_and_template_quoting():
    args = expand_command_template(
        'python "reviewer script.py" "" {diff_file} {case_json} {unknown} '
        "--id={case_id} --id={case_id}",
        case_json=Path("/tmp/{workspace}/case.json"),
        diff_file=Path('/tmp/a "quoted" diff.patch'),
        case_id="example",
        workspace=Path("/tmp/other"),
    )
    assert args == [
        "python",
        "reviewer script.py",
        "",
        '/tmp/a "quoted" diff.patch',
        "/tmp/{workspace}/case.json",
        "{unknown}",
        "--id=example",
        "--id=example",
    ]


def test_expand_command_template_rejects_unbalanced_template_quotes():
    with pytest.raises(ValueError, match="No closing quotation"):
        expand_command_template(
            'python "reviewer.py {case_json}',
            case_json=Path("/tmp/case.json"),
            diff_file=Path("/tmp/pr.diff"),
            case_id="example",
            workspace=Path("/tmp/workspace"),
        )
