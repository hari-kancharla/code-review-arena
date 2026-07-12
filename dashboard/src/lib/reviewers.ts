// Display-layer naming for reviewers. Internal IDs (e.g. "mock" + model
// "perfect_patch") stay as recorded in run/report data; this module only maps
// them to presentation labels for the dashboard.

export type ReviewerIdentity = { reviewer: string; model?: string | null };

const CONTROL_DISPLAY_NAMES: Record<string, string> = {
  perfect_patch: "Control: Perfect Repair",
  keyword_gamer: "Control: Keyword Gamer",
  bad_patch: "Control: Weak Repair",
  detects_no_patch: "Control: Detection Only",
  malformed_patch: "Control: Malformed Patch",
  false_positive_patch: "Control: False Positive",
};

const DISPLAY_NAMES: Record<string, string> = {
  "reference-patch": "Reference Patch",
  "custom-command": "Custom Command",
  "shallow-patch": "Shallow Patch",
};

export const CONTROL_BASELINE_NOTE =
  "Control baselines are deterministic harness checks, not external model results.";

/** Recovers a reviewer identity from a stored slug key, e.g. "mock:perfect_patch". */
export function identityFromSlug(slug: string): ReviewerIdentity {
  const separator = slug.indexOf(":");
  if (separator === -1) return { reviewer: slug };
  return {
    reviewer: slug.slice(0, separator),
    model: slug.slice(separator + 1),
  };
}

export function isControlBaseline({
  reviewer,
  model,
}: ReviewerIdentity): boolean {
  // "mock" is the pre-rename id kept for historical run data.
  return (reviewer === "control" || reviewer === "mock") && Boolean(model);
}

export function reviewerDisplayName(identity: ReviewerIdentity): string {
  const { reviewer, model } = identity;
  if ((reviewer === "control" || reviewer === "mock") && model) {
    return CONTROL_DISPLAY_NAMES[model] ?? `Control: ${titleCase(model)}`;
  }
  return DISPLAY_NAMES[reviewer] ?? reviewer;
}

function titleCase(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}
