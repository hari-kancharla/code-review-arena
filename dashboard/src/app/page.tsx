import Link from "next/link";

import { CodeBlock } from "../components/CodeBlock";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { fetchJson, type LeaderboardRow } from "../lib/api";
import {
  loadReportLeaderboardRows,
  readAuditReport,
  type AuditReport,
} from "../lib/auditReport";
import { CONTROL_BASELINE_NOTE, reviewerDisplayName } from "../lib/reviewers";

const evaluationStages = [
  {
    label: "Blind payload",
    detail: "Diff and bounded files, without ground-truth metadata.",
  },
  {
    label: "Reviewer output",
    detail: "Structured findings, confidence, evidence, and optional patch.",
  },
  {
    label: "Patch gate",
    detail: "The suggested repair must apply in an isolated workspace.",
  },
  {
    label: "Execution",
    detail: "Fixture-owned tests run with bounded resources.",
  },
  {
    label: "Validators",
    detail: "Structural checks confirm the expected repair shape.",
  },
  {
    label: "Score",
    detail: "Detection and validation are reported as separate signals.",
  },
];

const benchmarkPacks = [
  {
    id: "benchmark_sets/v1",
    label: "v1",
    cases: 10,
    purpose: "Baseline harness cases",
    validation: "review scoring + validation",
  },
  {
    id: "benchmark_sets/audit_v1",
    label: "audit_v1",
    cases: 10,
    purpose: "Patch-required audit failures",
    validation: "patch apply + tests + validators",
  },
  {
    id: "benchmark_sets/audit_v2",
    label: "audit_v2",
    cases: 10,
    purpose: "Certified logic-defect cases",
    validation: "patch apply + tests",
  },
  {
    id: "benchmark_sets/realfix_seed_v0",
    label: "realfix_seed_v0",
    cases: 3,
    purpose: "Historical-fix methodology seed",
    validation: "Docker-backed patch apply + tests",
  },
];

const trustControls = [
  "Keeps protected tests and unsafe paths outside reviewer patches.",
  "Captures run manifests, pack checksums, reviewer config, and timing.",
  "Makes local execution explicit, with Docker as the isolation-backed path.",
  "Keeps deterministic controls distinct from external model results.",
];

const runCommands = `python -m pip install -e ".[dev]"
arena validate benchmark_sets/audit_v1
arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --beta 1.0`;
const emptyCommands = `arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --beta 1.0`;

export default async function Home() {
  const liveRows = await fetchJson<LeaderboardRow[]>("/leaderboard").catch(
    () => [],
  );
  const auditV1 = readAuditReport("audit-v1.json").report;
  const auditV2 = readAuditReport("audit-v2.json").report;
  const reportSnapshots = [auditV1, auditV2].filter(
    (report): report is AuditReport => Boolean(report && !report.empty),
  );
  const auditRows = liveRows.some((row) => row.benchmark_set === "audit_v1")
    ? []
    : loadReportLeaderboardRows();
  const rows = [...auditRows, ...liveRows];
  const previewRows = rows
    .filter((row) => row.deterministic_metrics)
    .sort(
      (left, right) =>
        (right.deterministic_metrics?.validated_case_rate ?? 0) -
        (left.deterministic_metrics?.validated_case_rate ?? 0),
    )
    .slice(0, 5);
  const gapRows = rows
    .filter((row) => row.deterministic_metrics)
    .map((row) => ({
      row,
      gap:
        (row.deterministic_metrics?.detection_f_beta ?? 0) -
        (row.deterministic_metrics?.validated_case_rate ?? 0),
    }))
    .sort((left, right) => right.gap - left.gap)
    .slice(0, 3);
  const snapshotCases = reportSnapshots.reduce(
    (total, report) => total + report.summary.case_count,
    0,
  );
  const snapshotRuns = reportSnapshots.reduce(
    (total, report) => total + report.summary.run_count,
    0,
  );
  const largestSnapshotGap = reportSnapshots.reduce((gap, report) => {
    return Math.max(
      gap,
      report.summary.biggest_detection_validation_gap?.gap ?? 0,
    );
  }, 0);
  const benchmarkCaseCount = benchmarkPacks.reduce(
    (sum, pack) => sum + pack.cases,
    0,
  );
  const validatedRunCount = rows.filter(
    (row) => (row.deterministic_metrics?.validated_case_rate ?? 0) >= 0.8,
  ).length;

  return (
    <>
      <section className="overview-header">
        <div className="overview-title">
          <p className="eyebrow">Execution-backed code review benchmark</p>
          <h1>Code Review Arena</h1>
          <p>
            Compare what reviewers detect with the fixes they can apply,
            execute, and validate.
          </p>
        </div>
        <div className="overview-actions">
          <span className="snapshot-status">Audit snapshot ready</span>
          <div>
            <Link className="button primary" href="/leaderboard">
              Open leaderboard
            </Link>
            <Link className="button" href="/reports/audit-v2">
              Audit report
            </Link>
            <Link className="button text" href="/docs/getting-started">
              Run locally
            </Link>
          </div>
        </div>
      </section>

      <dl className="overview-stat-strip" aria-label="Benchmark snapshot">
        <div>
          <dt>Benchmark cases</dt>
          <dd>{benchmarkCaseCount}</dd>
          <small>across {benchmarkPacks.length} packs</small>
        </div>
        <div>
          <dt>Recorded runs</dt>
          <dd>{rows.length || "local"}</dd>
          <small>{snapshotRuns || 0} in audit snapshots</small>
        </div>
        <div>
          <dt>Validated runs</dt>
          <dd>{validatedRunCount}</dd>
          <small>at or above 0.800</small>
        </div>
        <div>
          <dt>Largest observed gap</dt>
          <dd>{formatRate(largestSnapshotGap || 1)}</dd>
          <small>detection minus validation</small>
        </div>
      </dl>

      <section className="overview-workspace section-large">
        <div className="workspace-panel results-workspace">
          <div className="workspace-head">
            <div>
              <p className="section-kicker">Recorded runs</p>
              <h2>Latest benchmark results</h2>
            </div>
            <Link href="/leaderboard">View all results</Link>
          </div>
          {previewRows.length ? (
            <LeaderboardPreview rows={previewRows} />
          ) : (
            <EmptyState
              title="No benchmark runs recorded"
              message="Generate a deterministic baseline locally to populate the leaderboard."
              command={emptyCommands}
            />
          )}
        </div>
        <BenchmarkArtifactPanel
          caseCount={snapshotCases}
          gap={largestSnapshotGap}
          rows={previewRows}
          runCount={snapshotRuns}
        />
      </section>

      <section className="section-large">
        <div className="section-head">
          <div>
            <p className="section-kicker">Evidence quality</p>
            <h2>See where detection becomes a working repair.</h2>
          </div>
          <Link href="/reports/audit-v1">Open audit v1</Link>
        </div>
        {gapRows.length ? (
          <div className="gap-bars">
            {gapRows.map(({ row, gap }) => (
              <MetricGapCard gap={gap} key={row.run_id} row={row} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="No gap data yet"
            message="Run a full-mode benchmark to compare detection against validated repairs."
            command={emptyCommands}
          />
        )}
      </section>

      <section className="benchmark-system section-large">
        <div className="system-panel metric-system">
          <div>
            <p className="section-kicker">Metric contract</p>
            <h2>Detection and validation are separate signals.</h2>
          </div>
          <div className="metric-contract">
            <article>
              <div>
                <span>Detection</span>
                <code>detection_f_beta</code>
              </div>
              <p>Finds and localizes the seeded issue.</p>
            </article>
            <div className="contract-flow" aria-label="Validation sequence">
              <span>finding</span>
              <span>patch apply</span>
              <span>tests</span>
              <span>validators</span>
            </div>
            <article className="primary">
              <div>
                <span>Validation</span>
                <code>validated_case_rate</code>
              </div>
              <p>
                Counts repairs that apply cleanly and pass every required
                check.
              </p>
            </article>
          </div>
        </div>
        <div className="system-panel pack-system">
          <div className="system-head">
            <div>
              <p className="section-kicker">Benchmark packs</p>
              <h2>Inspectable fixtures with hard evidence.</h2>
            </div>
            <Link href="/cases">Browse cases</Link>
          </div>
          <div className="pack-list">
            {benchmarkPacks.map((pack) => (
              <article key={pack.id}>
                <div>
                  <code>{pack.label}</code>
                  <strong>{pack.cases} cases</strong>
                </div>
                <p>{pack.purpose}</p>
                <small>{pack.validation}</small>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section-large">
        <div className="section-head">
          <div>
            <p className="section-kicker">Execution path</p>
            <h2>How a run becomes a benchmark result.</h2>
          </div>
        </div>
        <div className="pipeline evaluation-pipeline">
          {evaluationStages.map((stage, index) => (
            <div className="pipeline-node" key={stage.label}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{stage.label}</strong>
              <small>{stage.detail}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="operations-grid section-large">
        <div className="trust-section">
          <div>
            <p className="section-kicker">Integrity model</p>
            <h2>Trustworthy, reproducible evaluation.</h2>
          </div>
          <div className="trust-grid">
            {trustControls.map((control, index) => (
              <article className="trust-card" key={control}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <p>{control}</p>
              </article>
            ))}
          </div>
        </div>
        <div className="terminal-section">
          <div>
            <p className="section-kicker">Run locally</p>
            <h2>Reproduce the benchmark controls.</h2>
            <p>
              Local execution is explicit. Use it with benchmark fixtures you
              trust.
            </p>
          </div>
          <CodeBlock compact label="Terminal">
            {runCommands}
          </CodeBlock>
        </div>
      </section>
    </>
  );
}

function BenchmarkArtifactPanel({
  caseCount,
  gap,
  rows,
  runCount,
}: {
  caseCount: number;
  gap: number;
  rows: LeaderboardRow[];
  runCount: number;
}) {
  return (
    <aside className="artifact-panel">
      <div className="artifact-header">
        <p>Current evidence</p>
        <strong>patch-backed benchmark dossier</strong>
      </div>
      <div className="artifact-trace" aria-hidden="true">
        <div className="trace-window">
          <div>
            <span>case</span>
            <strong>security_sql_join_ownership_leak_001</strong>
          </div>
          <code>- JOIN documents ON owner_id = users.id</code>
          <code>+ JOIN documents ON owner_id = :tenant_user</code>
          <code className="trace-pass">tests/test_record_access.py passed</code>
        </div>
        <div className="validation-stack">
          <span>patch apply</span>
          <span>tests</span>
          <span>validators</span>
          <span>score</span>
        </div>
      </div>
      <dl className="artifact-stats">
        <div>
          <dt>Audit cases</dt>
          <dd>{caseCount || 20}</dd>
        </div>
        <div>
          <dt>Snapshot runs</dt>
          <dd>{runCount || "local"}</dd>
        </div>
        <div>
          <dt>Observed gap</dt>
          <dd>{formatRate(gap || 1)}</dd>
        </div>
      </dl>
      {rows.length ? (
        <p className="artifact-note">{CONTROL_BASELINE_NOTE}</p>
      ) : (
        <CodeBlock compact label="Generate runs">
          {emptyCommands}
        </CodeBlock>
      )}
    </aside>
  );
}

function MetricGapCard({ gap, row }: { gap: number; row: LeaderboardRow }) {
  const metrics = row.deterministic_metrics!;
  return (
    <article>
      <div className="gap-row-head">
        <strong>{reviewerDisplayName(row)}</strong>
        <StatusBadge tone={gap >= 0.5 ? "warning" : "success"}>
          {gap >= 0.5 ? "wide gap" : "aligned"}
        </StatusBadge>
      </div>
      <MetricBar
        label="Detected"
        value={metrics.detection_f_beta}
        tone="neutral"
      />
      <MetricBar
        label="Validated"
        value={metrics.validated_case_rate}
        tone="accent"
      />
      <p>
        {gap >= 0.5
          ? "This reviewer can identify the seeded issue, but the proposed repair does not validate."
          : "Detection and repair validation move together for this run."}
      </p>
    </article>
  );
}

function MetricBar({
  label,
  tone,
  value,
}: {
  label: string;
  tone: "accent" | "neutral";
  value: number;
}) {
  return (
    <div className={`report-bar ${tone === "accent" ? "accent" : ""}`}>
      <span>{label}</span>
      <i aria-hidden="true">
        <b style={{ width: formatRate(value) }} />
      </i>
      <strong>{value.toFixed(3)}</strong>
    </div>
  );
}

function LeaderboardPreview({ rows }: { rows: LeaderboardRow[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table preview-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Reviewer</th>
            <th>Benchmark</th>
            <th>
              Detection <span className="nowrap">F-beta</span>
            </th>
            <th>
              Validated <span className="nowrap">rate</span>
            </th>
            <th>Passes</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const metrics = row.deterministic_metrics!;
            const detectedOnly =
              metrics.detection_f_beta >= 0.8 &&
              metrics.validated_case_rate <= 0.3;
            return (
              <tr
                key={row.run_id}
                style={{ animationDelay: `${index * 40}ms` }}
              >
                <td className="numeric">{index + 1}</td>
                <td>
                  <strong>{reviewerDisplayName(row)}</strong>
                </td>
                <td>
                  <code>{row.benchmark_set ?? "recorded"}</code>
                </td>
                <td className="numeric">
                  {metrics.detection_f_beta.toFixed(3)}
                </td>
                <td className="numeric strong-metric">
                  {metrics.validated_case_rate.toFixed(3)}
                </td>
                <td className="numeric">
                  {row.deterministic_passes}/{row.case_count}
                </td>
                <td>
                  <StatusBadge
                    tone={
                      detectedOnly
                        ? "warning"
                        : metrics.validated_case_rate >= 0.8
                          ? "success"
                          : "danger"
                    }
                  >
                    {detectedOnly
                      ? "detected only"
                      : metrics.validated_case_rate >= 0.8
                        ? "validated"
                        : "not validated"}
                  </StatusBadge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function formatRate(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}
