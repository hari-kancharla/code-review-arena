import Link from "next/link";
import { FailureReasonList } from "../../../components/FailureReasonList";
import { FailureReasonChart } from "../../../components/FailureReasonChart";
import { MetricCard } from "../../../components/MetricCard";
import { PageHeader } from "../../../components/PageHeader";
import { StatusBadge } from "../../../components/StatusBadge";
import { ValidationFunnel } from "../../../components/ValidationFunnel";
import { fetchJson, RunDetail } from "../../../lib/api";

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await fetchJson<RunDetail>(`/runs/${id}`);
  const metrics = run.deterministic_metrics;
  const reasons = run.case_results.flatMap((result) => result.failure_reasons);
  const reasonCounts = Array.from(
    reasons.reduce((counts, reason) => counts.set(reason, (counts.get(reason) ?? 0) + 1), new Map<string, number>()),
  );
  return (
    <>
      <PageHeader
        eyebrow={`${run.benchmark_set} / ${run.mode} / ${run.run_id}`}
        title={`${run.reviewer}:${run.model || "default"}`}
        description="A complete validation trace from seeded defect detection through patch execution and structural checks."
        actions={runBadge(run)}
      />
      <section className="run-meta panel">
        <span><strong>Reviewer</strong>{run.reviewer}</span>
        <span><strong>Model</strong>{run.model || "default"}</span>
        <span><strong>Benchmark</strong>{run.benchmark_set}</span>
        <span><strong>Mode</strong>{run.mode}</span>
        <span><strong>Completed</strong>{new Date(run.completed_at).toLocaleString()}</span>
        <span><strong>Duration</strong>{(run.total_latency_ms / 1000).toFixed(2)}s</span>
        <span><strong>Prompt version</strong>{run.metadata.prompt_version}</span>
        <span><strong>Commit</strong>{run.metadata.git_commit ?? "not recorded"}</span>
      </section>
      {metrics ? (
        <>
          <section className="metrics-eight">
            <MetricCard label="Validated case rate" value={rate(metrics.validated_case_rate)} note="cases fully repaired" emphasis />
            <MetricCard label="Detection F-beta" value={metrics.detection_f_beta.toFixed(3)} />
            <MetricCard label="Deterministic Pass Rate" value={rate(metrics.deterministic_pass_rate)} />
            <MetricCard label="Patch Apply Rate" value={rate(metrics.patch_apply_rate)} />
            <MetricCard label="Structural Pass Rate" value={rate(metrics.structural_pass_rate)} />
            <MetricCard label="Test Pass Rate" value={rate(metrics.test_pass_rate)} />
            <MetricCard label="False Positives / Case" value={metrics.false_positives_per_case.toFixed(2)} />
            <MetricCard label="Cost / Validated Fix" value={currency(metrics.cost_per_validated_fix)} />
          </section>
          <section className="panel section-space">
            <h2>Validation funnel</h2>
            <ValidationFunnel cases={run.case_results} />
          </section>
          <section className="grid two-columns section-space">
            <div className="panel">
              <h2>Failure reason breakdown</h2>
              <FailureReasonChart counts={Object.fromEntries(reasonCounts)} />
            </div>
            <div className="panel">
              <h2>Outcome interpretation</h2>
              {metrics.validated_case_rate == null ? (
                <p className="callout">No execution-backed case in this run, so repair was not measured.</p>
              ) : metrics.detection_f_beta > metrics.validated_case_rate ? (
                <p className="callout warning">Detected only - fix not validated. The reviewer identified issues that did not complete execution-backed validation.</p>
              ) : <p className="pass-text">Detection and validated repair outcomes align for this run.</p>}
              <FailureReasonList reasons={Array.from(new Set(reasons))} />
            </div>
          </section>
        </>
      ) : (
        <section className="panel section-space"><p>This review-only run has no patch validation metrics.</p></section>
      )}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Category</th>
              <th>Severity</th>
              <th>Detected</th>
              <th>Localized</th>
              <th>Patch</th>
              <th>Tests</th>
              <th>Validators</th>
              <th>Outcome</th>
              <th>Failure Reasons</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {run.case_results.map((item) => (
              <tr key={item.case_id}>
                <td><Link href={`/runs/${run.run_id}/cases/${item.case_id}`}><strong>{item.case_id}</strong></Link></td>
                <td>{item.category}</td>
                <td>{item.severity}</td>
                <td>{status(item.bug_found, true)}</td>
                <td>{status(item.correct_file && item.correct_line, true)}</td>
                <td>{status(item.patch_applied, item.patch_provided)}</td>
                <td>{status(item.tests_passed, item.tests_ran)}</td>
                <td>{status(item.validators_passed, item.validators_run.length > 0)}</td>
                <td>{status(item.deterministic_pass, item.deterministic_pass != null)}</td>
                <td className="failure-cell">{item.failure_reasons.join(", ") || "-"}</td>
                <td><Link href={`/runs/${run.run_id}/cases/${item.case_id}`}>View trace</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function status(value: boolean | null, applicable: boolean) {
  if (!applicable) return <StatusBadge tone="neutral">Skipped</StatusBadge>;
  return <StatusBadge tone={value ? "success" : "danger"}>{value ? "Pass" : "Fail"}</StatusBadge>;
}

function rate(value: number | null) {
  return value == null ? "-" : `${(value * 100).toFixed(1)}%`;
}

function currency(value: number | null) {
  return value == null ? "-" : `$${value.toFixed(4)}`;
}

function runBadge(run: RunDetail) {
  const metrics = run.deterministic_metrics;
  if (!metrics) return <StatusBadge tone="neutral">Review only</StatusBadge>;
  // Not measured is not a failure: the run had no execution-backed case.
  if (metrics.validated_case_rate == null)
    return <StatusBadge tone="neutral">Not measured</StatusBadge>;
  if (metrics.validated_case_rate === 1) return <StatusBadge tone="success">Validated</StatusBadge>;
  if (metrics.detection_f_beta > metrics.validated_case_rate) {
    return <StatusBadge tone="warning">Detected only - fix not validated</StatusBadge>;
  }
  return <StatusBadge tone="danger">Validation failed</StatusBadge>;
}
