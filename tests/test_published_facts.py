"""Facts published in documentation must agree with the code that defines them.

A fact copied out of the code into prose is a fact that will rot, and several
already had: the structural-validator list omitted three registered validators,
and the pack table credited a pack with a case count it no longer had. Prose
cannot be kept honest by intention alone, so the copies that matter are asserted
here against their single source of truth.

Pack case counts have their own guard in test_dashboard_pack_table.py.
"""

import re
from pathlib import Path

from arena.reports.leaderboard import eligibility_from_fields
from arena.validators.registry import _VALIDATORS

DOCS = Path("docs")
README = Path("README.md")

_BULLET = re.compile(r"^- `([a-z0-9_]+)`", re.MULTILINE)


def _documented_validators() -> set[str]:
    text = (DOCS / "structural-validators.md").read_text(encoding="utf-8")
    section = text.split("## Included Validators", 1)
    assert len(section) == 2, "the Included Validators section is gone"
    return set(_BULLET.findall(section[1]))


def test_every_registered_validator_is_documented():
    """An undocumented validator is one a case author cannot know exists."""
    assert _documented_validators() == set(_VALIDATORS)


def test_no_documented_validator_is_unregistered():
    """The inverse: prose must not advertise a validator a case cannot name."""
    assert not _documented_validators() - set(_VALIDATORS)


def _eligible(**overrides: object) -> bool:
    fields: dict[str, object] = {
        "schema_version": 2,
        "run_status": "complete",
        "execution_backend": "docker",
        "coverage_rate": 1.0,
        "pack_digest_externally_verified": True,
        "non_exact_output_used": False,
        "reviewer_oracle_reachable": False,
    }
    fields.update(overrides)
    return eligibility_from_fields(**fields)  # type: ignore[arg-type]


def test_docker_alone_does_not_make_a_run_leaderboard_eligible():
    """The README and DEMO.md both claimed it did.

    Docker is necessary but not sufficient, and saying otherwise tells an
    operator their run will be ranked when it will not be. Each remaining
    requirement is asserted so the docs' list of them cannot silently go stale.
    """
    assert _eligible() is True

    assert _eligible(execution_backend="local") is False
    assert _eligible(coverage_rate=0.9) is False
    assert _eligible(pack_digest_externally_verified=False) is False
    assert _eligible(non_exact_output_used=True) is False
    assert _eligible(reviewer_oracle_reachable=True) is False
    # Unknown is treated as unsafe, not as absent.
    assert _eligible(reviewer_oracle_reachable=None) is False
    assert _eligible(non_exact_output_used=None) is False


def test_docs_do_not_claim_docker_alone_is_sufficient():
    for path in (README, DOCS / "DEMO.md"):
        text = path.read_text(encoding="utf-8")
        assert "Docker-backed runs are leaderboard-eligible without" not in text
        assert "(Docker-backed runs appear without it)" not in text


_SCHEMA_BLOCK = re.compile(r"const schema = `(?P<json>\{.*?\})`;", re.DOTALL)


def test_the_published_reviewer_schema_parses_as_exact():
    """The documented reviewer response must be one the harness accepts.

    The published example omitted summary, category, severity and evidence, all
    of which a Finding requires, so it parsed as `invalid` -- meaning anyone who
    implemented a reviewer from the documentation earned the invalid-output
    penalty on every case while following the instructions exactly.
    """
    from arena.reviewers.response_parser import parse_reviewer_output

    page = Path("dashboard/src/app/docs/adding-reviewers/page.tsx")
    match = _SCHEMA_BLOCK.search(page.read_text(encoding="utf-8"))
    assert match, "the reviewer schema example is gone from the docs page"

    outcome = parse_reviewer_output(match.group("json"))

    assert outcome.status == "exact", f"published schema parses as {outcome.status}"
    assert outcome.retained_finding_count == 1


def test_the_case_catalogue_fetches_every_shipped_pack():
    """A pack absent from the catalogue is invisible to anyone browsing it.

    `realfix_seed_v0` shipped, validated, certified and ran in CI while the page
    fetched only the other three, so the one pack built from real historical
    fixes was the one nobody could see.
    """
    page = Path("dashboard/src/app/cases/page.tsx").read_text(encoding="utf-8")
    fetched = set(re.findall(r"/cases\?benchmark_set=([A-Za-z0-9_]+)", page))
    shipped = {
        path.name for path in Path("benchmark_sets").iterdir() if (path / "manifest.yaml").is_file()
    }

    assert fetched == shipped
