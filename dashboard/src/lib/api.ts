const api = process.env.ARENA_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${api}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export type LeaderboardRow = {
  reviewer: string;
  model: string;
  mode: "review" | "patch" | "full";
  score: number;
  bugs_found: number;
  case_count: number;
  false_positives: number;
  cost: number;
  latency_ms: number;
  run_id: string;
  history_count: number;
  completed_at: string;
  benchmark_set?: string;
  detail_available?: boolean;
  deterministic_passes: number;
  // Denominator for deterministic_passes: the execution-backed cases only,
  // which is what validated_case_rate is computed over. Optional because rows
  // stored before this field existed carry no value; fall back to case_count.
  validated_eligible_case_count?: number | null;
  deterministic_metrics: DeterministicMetrics | null;
  // Whether this run meets the default comparability policy (Docker-backed, full
  // coverage, externally verified pack, exact reviewer output). Absent on rows
  // from an API predating the field; only `false` asserts "not comparable".
  verified?: boolean;
  // Where the row came from. "shipped-snapshot" rows are the committed audit
  // results that ship with the repo, NOT the viewer's own runs, and must always be
  // labelled as such rather than presented as live results.
  source?: "live" | "shipped-snapshot";
};

export type DeterministicMetrics = {
  detection_precision: number;
  detection_recall: number;
  detection_f1: number;
  detection_f_beta: number;
  validated_precision: number;
  validated_recall: number;
  validated_f1: number;
  validated_f_beta: number;
  beta: number;
  // null when the run had no execution-backed case: the rate was not measured,
  // which is different from a reviewer that repaired nothing.
  deterministic_pass_rate: number | null;
  validated_case_rate: number | null;
  complete_repair_rate: number | null;
  bug_completeness_rate: number;
  supported_claim_rate: number | null;
  patch_apply_rate: number | null;
  test_pass_rate: number | null;
  structural_pass_rate: number | null;
  false_positives_per_case: number;
  cost_per_validated_fix: number | null;
  latency_per_case_ms: number;
};

export type CaseSummary = {
  id: string;
  benchmark_set: "v1" | "audit_v1" | "audit_v2";
  title: string;
  category: string;
  severity: string;
  stack: string[];
  ground_truth_summary: string;
  validation: {
    patch_required: boolean;
    tests_required: boolean;
    structural_validators: string[];
  };
  execution: {
    run_tests: boolean;
    test_command: string | null;
  };
};

export type Breakdown = {
  concept_match: number;
  file_match: number;
  line_overlap: number;
  severity_match: number;
  fix_quality: number;
  no_false_positives: number;
  false_positive_penalty: number;
  invalid_json_penalty: number;
  total: number;
};

export type Finding = {
  title: string;
  summary: string;
  category: string;
  severity: string;
  file: string;
  line_start: number;
  line_end: number;
  evidence: string;
  suggested_fix: string | null;
  suggested_patch: string | null;
  replacement_code: string | null;
  patch_confidence: number | null;
  confidence: number;
};

export type CaseResult = {
  case_id: string;
  title: string;
  category: string;
  severity: string;
  ground_truth_summary: string;
  score: number;
  bug_found: boolean;
  correct_file: boolean;
  correct_line: boolean;
  line_match: string;
  false_positive_count: number;
  test_output: string;
  review_quality_score: number | null;
  patch_provided: boolean;
  patch_applied: boolean;
  patch_error: string | null;
  touched_files: string[];
  tests_ran: boolean;
  tests_passed: boolean | null;
  test_stdout_tail: string;
  test_stderr_tail: string;
  validators_run: string[];
  validators_passed: boolean | null;
  validator_results: Array<{
    name: string;
    passed: boolean;
    confidence: number;
    message: string;
    evidence: string[];
    error: string | null;
  }>;
  deterministic_pass: boolean | null;
  failure_reasons: string[];
  raw_suggested_patch: string | null;
  breakdown: Breakdown;
  scored_findings: Array<{
    finding: Finding;
    is_true_positive: boolean;
    false_positive_reason: string | null;
  }>;
};

export type RunDetail = {
  run_id: string;
  benchmark_set: string;
  reviewer: string;
  model: string;
  total_score: number;
  bugs_found: number;
  correct_files: number;
  correct_lines: number;
  false_positives: number;
  total_cost: number;
  total_latency_ms: number;
  deterministic_metrics: DeterministicMetrics | null;
  mode: "review" | "patch" | "full";
  beta: number;
  started_at: string;
  completed_at: string;
  metadata: {
    prompt_version: string;
    benchmark_version: string;
    temperature: number;
    git_commit: string | null;
  };
  case_results: CaseResult[];
};

export type RunSummary = {
  id: string;
  reviewer: string;
  model: string;
  benchmark_set: string;
  total_score: number;
  total_cost: number;
  total_latency_ms: number;
  completed_at: string;
  beta: number | null;
  detection_f_beta: number | null;
  validated_f_beta: number | null;
  validated_case_rate: number | null;
  deterministic_pass_rate: number | null;
  patch_apply_rate: number | null;
  structural_pass_rate: number | null;
  test_pass_rate: number | null;
  // Run validity. Nullable because rows written before v2 carry no validity at
  // all; the badge treats unknown as untrusted rather than as success.
  schema_version: number | null;
  run_status: string | null;
  execution_backend: string | null;
  coverage_rate: number | null;
  completed_case_count: number | null;
  failed_case_count: number | null;
  skipped_case_count: number | null;
  eligible_case_count: number | null;
};

export type CaseTrace = CaseResult & {
  diff: string;
  stack: string[];
  ground_truth: {
    primary_bug: {
      summary: string;
      files: Array<{ path: string; line_ranges: Array<{ start: number; end: number }> }>;
      concepts: string[];
      acceptable_fix_keywords: string[];
    };
  };
};
