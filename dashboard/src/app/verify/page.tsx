import fs from "fs";
import Link from "next/link";
import path from "path";

import { CodeBlock } from "../../components/CodeBlock";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { EXPECTED_REPORT_SCHEMA_VERSION } from "../../lib/auditReport";
import { identityFromSlug, reviewerDisplayName } from "../../lib/reviewers";

type Check = {
  status: "passing" | "failing" | "unknown";
  checked_at: string | null;
  command: string;
  explanation: string;
};
type Baseline = {
  status: Check["status"];
  checked_at: string | null;
  command: string;
  meaning: string;
  run_id: string | null;
  metrics: null | {
    detection_f_beta: number | null;
    validated_f_beta: number | null;
    validated_case_rate: number | null;
    deterministic_passes: number;
    validated_eligible_case_count?: number | null;
    case_count: number;
  };
};
type Snapshot = {
  schema_version: string;
  project_name: string;
  generated_at: string;
  benchmark_sets: Record<string, Check>;
  quality_checks: Record<string, Check>;
  capabilities: Record<string, Check>;
  baselines: Record<string, Baseline>;
};

const reproduce = `python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
arena validate benchmark_sets/audit_v1
arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:keyword_gamer --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified`;

function loadSnapshot(): { snapshot: Snapshot | null; error: string | null } {
  const file = path.join(process.cwd(), "public", "verification.json");
  if (!fs.existsSync(file)) return { snapshot: null, error: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    return {
      snapshot: null,
      error: `verification.json is not valid JSON (${detail}). Regenerate it with scripts/generate_verification_snapshot.py.`,
    };
  }
  const version = (parsed as { schema_version?: unknown }).schema_version;
  if (version !== EXPECTED_REPORT_SCHEMA_VERSION) {
    return {
      snapshot: null,
      error:
        `verification.json schema_version ${JSON.stringify(version)} does not match the ` +
        `expected ${JSON.stringify(EXPECTED_REPORT_SCHEMA_VERSION)}. ` +
        `Regenerate it with scripts/generate_verification_snapshot.py.`,
    };
  }
  return { snapshot: parsed as Snapshot, error: null };
}

export default function VerifyPage() {
  const { snapshot, error } = loadSnapshot();
  if (error) {
    return (
      <>
        <PageHeader
          eyebrow="Verification / Local evidence"
          title="Project Health"
          description="A generated snapshot of reproducible checks and deterministic control outcomes."
        />
        <section className="panel empty section-space">
          <h2>Verification snapshot could not be read</h2>
          <p>{error}</p>
        </section>
      </>
    );
  }
  return (
    <>
      <PageHeader
        eyebrow="Verification / Local evidence"
        title="Project Health"
        description="A generated snapshot of reproducible checks and deterministic control outcomes. Unknown means the command was not recorded in this snapshot."
        actions={
          snapshot ? (
            <span className="verification-time">
              Generated {new Date(snapshot.generated_at).toLocaleString()}
            </span>
          ) : undefined
        }
      />
      {!snapshot ? (
        <section className="panel empty section-space">
          <h2>No verification snapshot generated yet</h2>
          <p>
            Generate a local evidence snapshot; checks not executed remain
            explicitly unknown.
          </p>
          <CodeBlock compact>
            python scripts/generate_verification_snapshot.py --run-validation
            --run-quality-checks --generate-report
          </CodeBlock>
        </section>
      ) : (
        <>
          <section className="health-grid">
            <HealthCard
              label="Backend tests"
              check={snapshot.quality_checks.tests}
            />
            <HealthCard label="Lint" check={snapshot.quality_checks.lint} />
            <HealthCard
              label="Typecheck"
              check={snapshot.quality_checks.typecheck}
            />
            <HealthCard
              label="Dashboard build"
              check={snapshot.quality_checks.dashboard_build}
            />
            <HealthCard
              label="audit_v1 validation"
              check={snapshot.benchmark_sets.audit_v1}
            />
            <BaselineHealth
              label="Reference Patch baseline"
              baseline={snapshot.baselines["reference-patch"]}
            />
            <BaselineHealth
              label="Control: Keyword Gamer (adversarial) baseline"
              baseline={snapshot.baselines["control:keyword_gamer"]}
            />
            <HealthCard
              label="Audit report generation"
              check={snapshot.capabilities.audit_report_generation}
            />
            <HealthCard
              label="Custom-command reviewer"
              check={snapshot.capabilities.custom_command_reviewer}
            />
          </section>
          <section className="panel section-space">
            <h2>Baseline Matrix</h2>
            <p className="matrix-note">
              These rows are deterministic controls from saved local{" "}
              <code>audit_v1</code> runs, not real model performance claims.
            </p>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Reviewer</th>
                    <th>Type</th>
                    <th>Detection F-beta</th>
                    <th>Validated rate</th>
                    <th>Deterministic Passes</th>
                    <th>Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(snapshot.baselines).map(([slug, row]) => {
                    const identity = identityFromSlug(slug);
                    return (
                      <tr key={slug}>
                        <td>
                          <strong>{reviewerDisplayName(identity)}</strong>
                        </td>
                        <td>
                          <StatusBadge tone="neutral">Control</StatusBadge>
                        </td>
                        <td>{metric(row.metrics?.detection_f_beta)}</td>
                        <td className="strong-metric">
                          {metric(row.metrics?.validated_case_rate)}
                        </td>
                        <td>
                          {row.metrics
                            ? `${row.metrics.deterministic_passes}/${row.metrics.validated_eligible_case_count ?? row.metrics.case_count}`
                            : "-"}
                        </td>
                        <td>{row.meaning}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      <section className="grid two-columns">
        <div className="panel">
          <h2>Reproduce Locally</h2>
          <CodeBlock compact>{reproduce}</CodeBlock>
        </div>
        <div className="panel">
          <h2>Troubleshooting</h2>
          <p className="subtitle">
            Installation, stale dashboard data, missing reports, and empty-run
            guidance are documented step by step.
          </p>
          <Link className="button" href="/docs/troubleshooting">
            Open troubleshooting -&gt;
          </Link>
        </div>
      </section>
    </>
  );
}

function HealthCard({ label, check }: { label: string; check: Check }) {
  return (
    <article className="panel health-card">
      <StatusBadge tone={tone(check.status)}>{check.status}</StatusBadge>
      <h3>{label}</h3>
      <p>{check.explanation}</p>
      <p className="command">{check.command}</p>
      <p>
        {check.checked_at
          ? `Checked ${new Date(check.checked_at).toLocaleString()}`
          : "Not checked in this snapshot"}
      </p>
    </article>
  );
}

function BaselineHealth({
  label,
  baseline,
}: {
  label: string;
  baseline: Baseline | undefined;
}) {
  if (!baseline) {
    return (
      <HealthCard
        label={label}
        check={{
          status: "unknown",
          checked_at: null,
          command: "",
          explanation: "No saved audit_v1 control run found.",
        }}
      />
    );
  }
  return (
    <HealthCard
      label={label}
      check={{
        status: baseline.status,
        checked_at: baseline.checked_at,
        command: baseline.command,
        explanation: baseline.metrics
          ? `${baseline.metrics.deterministic_passes}/${baseline.metrics.validated_eligible_case_count ?? baseline.metrics.case_count} deterministic passes; validated case rate ${metric(baseline.metrics.validated_case_rate)}.`
          : "No saved audit_v1 control run found.",
      }}
    />
  );
}

function metric(value: number | null | undefined) {
  return value == null ? "-" : value.toFixed(3);
}

function tone(status: Check["status"]) {
  return status === "passing"
    ? "success"
    : status === "failing"
      ? "danger"
      : "neutral";
}
