from arena.benchmark.case_loader import build_context, load_case, load_cases
from arena.benchmark.ground_truth import reviewer_safe_metadata
from tests.conftest import pack_case_count


def test_loads_every_case_the_manifest_declares(benchmark_dir):
    cases = load_cases(benchmark_dir)
    assert len(cases) == pack_case_count()
    assert cases[0].id == "fastapi_auth_bypass_001"


def _write_image_inheritance_pack(root, *, case_image=None):
    pack = root / "pack"
    case = pack / "c1"
    case.mkdir(parents=True)
    image_line = (
        f"\nexecution: {{run_tests: true, test_command: pytest, docker_image: {case_image}}}"
    )
    (case / "case.yaml").write_text(
        "id: c1\n"
        "title: t\n"
        "category: correctness\n"
        "severity: high\n"
        "stack: [python]\n"
        "description: d\n"
        "input: {}\n"
        "ground_truth:\n"
        "  bugs:\n"
        "    - summary: b\n"
        "      files: [{path: a.py, line_ranges: [{start: 1, end: 1}]}]\n"
        "      concepts: [correctness]\n" + (image_line if case_image else "")
    )
    (pack / "manifest.yaml").write_text(
        "version: img_v1\nname: img\ncases: [c1]\ndefault_docker_image: arena-bench:1\n"
    )
    return pack


def test_manifest_default_docker_image_is_inherited(tmp_path):
    pack = _write_image_inheritance_pack(tmp_path)
    case = load_cases(pack)[0]
    assert case.execution.docker_image == "arena-bench:1"


def test_case_docker_image_overrides_the_manifest_default(tmp_path):
    pack = _write_image_inheritance_pack(tmp_path, case_image="custom:7")
    case = load_cases(pack)[0]
    assert case.execution.docker_image == "custom:7"


def test_load_case_alone_does_not_apply_the_manifest_default(tmp_path):
    # Inheritance is a pack-level concern; a bare load_case has no manifest.
    pack = _write_image_inheritance_pack(tmp_path)
    case = load_case(pack / "c1")
    assert case.execution.docker_image is None


def test_context_has_diff_and_files_but_metadata_helper_hides_answer(benchmark_dir):
    case = load_cases(benchmark_dir)[0]
    context = build_context(case)
    assert "delete_user" in context.diff
    assert "app/routes/admin.py" in context.relevant_files
    assert not hasattr(context.case, "ground_truth")
    assert "ground_truth" not in reviewer_safe_metadata(case)
