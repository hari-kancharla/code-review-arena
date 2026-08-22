import Link from "next/link";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { fetchJson, RunSummary } from "../../lib/api";

export default async function Runs() {
  const runs = await fetchJson<RunSummary[]>("/runs").catch(() => []);
  return (
    <>
      <PageHeader
        eyebrow="Execution history"
        title="Benchmark runs"
        description="Inspect detection, patch application, tests, structural validators, cost, and latency for every run."
      />
      {runs.length === 0 ? (
        <EmptyState
          title="No runs to show"
          message="Runs are read from the API. Start it with `arena serve`, then generate a run."
          command={
            "arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution"
          }
        />
      ) : (
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Reviewer / Model</th>
              <th>Status</th>
              <th className="numeric">Validated rate</th>
              <th className="numeric">Detection F-beta</th>
              <th className="numeric">Pass Rate</th>
              <th className="numeric">Quality</th>
              <th className="numeric">Latency</th>
              <th>Completed</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td><Link href={`/runs/${run.id}`}>{run.id}</Link></td>
                <td>{run.reviewer}:{run.model || "default"}</td>
                <td>{runStatus(run)}</td>
                <td className="numeric strong-metric">{number(run.validated_case_rate)}</td>
                <td className="numeric">{number(run.detection_f_beta)}</td>
                <td className="numeric">{rate(run.deterministic_pass_rate)}</td>
                <td className="numeric">{run.total_score.toFixed(1)}</td>
                <td className="numeric">{(run.total_latency_ms / 1000).toFixed(2)}s</td>
                <td>{new Date(run.completed_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </>
  );
}

function runStatus(run: RunSummary) {
  // Validity outranks the score. A run the harness rejected -- a tampered pack,
  // a backend that never executed, cases that crashed -- must never render as a
  // success, however good its numbers look: on an invalid run the numbers are
  // precisely what cannot be trusted.
  if (run.run_status && run.run_status !== "complete") {
    const tone = run.run_status === "invalid" || run.run_status === "failed" ? "danger" : "warning";
    return <StatusBadge tone={tone}>{`Not comparable - ${run.run_status}`}</StatusBadge>;
  }
  if (run.validated_case_rate == null) return <StatusBadge tone="neutral">Review only</StatusBadge>;
  if (run.validated_case_rate === 1) return <StatusBadge tone="success">Validated</StatusBadge>;
  if ((run.detection_f_beta ?? 0) > run.validated_case_rate) {
    return <StatusBadge tone="warning">Detected only - fix not validated</StatusBadge>;
  }
  return <StatusBadge tone="danger">Validation failed</StatusBadge>;
}

function number(value: number | null) {
  return value == null ? "-" : value.toFixed(3);
}

function rate(value: number | null) {
  return value == null ? "-" : `${(value * 100).toFixed(1)}%`;
}
