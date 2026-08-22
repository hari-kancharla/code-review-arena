const labels: Record<string, string> = {
  detection_failed: "Seeded bug was not detected",
  incomplete_bug_detection: "Only some of the seeded bugs were found",
  localization_failed: "Finding was not localized to the expected code",
  patch_required_but_missing: "Patch required but missing",
  patch_apply_failed: "Patch did not apply cleanly",
  patch_unsafe_paths: "Patch referenced a path outside the workspace",
  patch_touched_protected_files: "Patch modified the tests or another protected file",
  tests_failed: "Regression tests did not pass",
  // Distinct from tests_failed on purpose: the harness never obtained a verdict
  // (output cap, missing runner, missing Docker), so this is not the reviewer's
  // repair being rejected.
  execution_inconclusive: "Tests could not be run to a verdict",
  test_integrity_violation: "The hidden tests changed during the run",
  no_execution_evidence: "No executable gate could confirm the repair",
  structural_validation_failed: "Structural validation failed",
  false_positive: "Unmatched finding exceeded the allowed threshold",
};

// Short form for compact contexts (report cards, chart labels) where the
// long descriptive sentences above would wrap or crowd the layout.
const shortLabels: Record<string, string> = {
  detection_failed: "Detection failed",
  incomplete_bug_detection: "Bugs missed",
  localization_failed: "Localization failed",
  patch_required_but_missing: "Patch missing",
  patch_apply_failed: "Patch apply failed",
  patch_unsafe_paths: "Unsafe patch path",
  patch_touched_protected_files: "Touched protected files",
  tests_failed: "Tests failed",
  execution_inconclusive: "No test verdict",
  test_integrity_violation: "Tests were altered",
  no_execution_evidence: "No execution evidence",
  structural_validation_failed: "Structural validation failed",
  false_positive: "False positive",
};

export function shortFailureLabel(reason: string): string {
  return shortLabels[reason] ?? reason.replaceAll("_", " ");
}

export function FailureReasonList({ reasons }: { reasons: string[] }) {
  if (!reasons.length)
    return <p className="pass-text">No validation failures.</p>;
  return (
    <ul className="reason-list">
      {reasons.map((reason) => (
        <li key={reason}>
          <code>{reason}</code>
          <span>{labels[reason] ?? reason.replaceAll("_", " ")}</span>
        </li>
      ))}
    </ul>
  );
}
