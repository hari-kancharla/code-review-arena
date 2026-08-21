"use client";

import Link from "next/link";
import { useDeferredValue, useMemo, useState } from "react";
import type { DeterministicMetrics, LeaderboardRow } from "../lib/api";
import {
  CONTROL_BASELINE_NOTE,
  isControlBaseline,
  reviewerDisplayName,
} from "../lib/reviewers";
import { EmptyState } from "./EmptyState";
import { StatusBadge } from "./StatusBadge";

type Metric =
  | "validated_case_rate"
  | "detection_f_beta"
  | "complete_repair_rate"
  | "bug_completeness_rate"
  | "supported_claim_rate"
  | "deterministic_pass_rate"
  | "patch_apply_rate"
  | "test_pass_rate"
  | "structural_pass_rate"
  | "false_positives_per_case"
  | "latency_per_case_ms";

const metricOptions: Metric[] = [
  "validated_case_rate",
  "detection_f_beta",
  "complete_repair_rate",
  "bug_completeness_rate",
  "supported_claim_rate",
  "deterministic_pass_rate",
  "patch_apply_rate",
  "test_pass_rate",
  "structural_pass_rate",
  "false_positives_per_case",
  "latency_per_case_ms",
];
const emptyCommand = `arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --beta 1.0`;

export function LeaderboardTable({ rows }: { rows: LeaderboardRow[] }) {
  const [metric, setMetric] = useState<Metric>("validated_case_rate");
  const [beta, setBeta] = useState(1);
  const [benchmark, setBenchmark] = useState(
    rows.some((row) => row.benchmark_set === "audit_v1")
      ? "audit_v1"
      : (rows.find((row) => row.benchmark_set)?.benchmark_set ?? ""),
  );
  const [reviewer, setReviewer] = useState("");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const benchmarkOptions = Array.from(
    new Set(
      rows
        .map((row) => row.benchmark_set)
        .filter((item): item is string => Boolean(item)),
    ),
  ).sort();
  const reviewerOptions = Array.from(
    new Set(rows.map((row) => reviewerDisplayName(row))),
  ).sort();
  const ascending =
    metric === "false_positives_per_case" || metric === "latency_per_case_ms";
  const sorted = useMemo(
    () =>
      rows
        .filter(
          (row) =>
            !benchmark || !row.benchmark_set || row.benchmark_set === benchmark,
        )
        .filter((row) => !reviewer || reviewerDisplayName(row) === reviewer)
        .filter(
          (row) =>
            !deferredQuery ||
            `${reviewerDisplayName(row)} ${row.model} ${row.benchmark_set} ${row.run_id}`
              .toLowerCase()
              .includes(deferredQuery.toLowerCase()),
        )
        .sort((left, right) => {
          const leftValue = value(left, metric, beta);
          const rightValue = value(right, metric, beta);
          if (leftValue == null) return rightValue == null ? 0 : 1;
          if (rightValue == null) return -1;
          const delta = leftValue - rightValue;
          return ascending ? delta : -delta;
        }),
    [ascending, benchmark, beta, deferredQuery, metric, reviewer, rows],
  );

  if (!rows.length) {
    return (
      <EmptyState
        title="No completed runs yet"
        message="Run a benchmark locally to generate leaderboard results."
        command={emptyCommand}
      />
    );
  }

  return (
    <>
      <div className="controls filter-bar">
        <label>
          Benchmark
          <select
            value={benchmark}
            onChange={(event) => setBenchmark(event.target.value)}
          >
            <option value="">All</option>
            {benchmarkOptions.map((option) => (
              <option value={option} key={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label>
          Reviewer
          <select
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
          >
            <option value="">All reviewers</option>
            {reviewerOptions.map((option) => (
              <option value={option} key={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label>
          Metric
          <select
            value={metric}
            onChange={(event) => setMetric(event.target.value as Metric)}
          >
            {metricOptions.map((option) => (
              <option value={option} key={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label>
          Beta
          <select
            value={beta}
            onChange={(event) => setBeta(Number(event.target.value))}
          >
            <option value={0.5}>0.5 precision</option>
            <option value={1}>1.0 balanced</option>
            <option value={2}>2.0 recall</option>
          </select>
        </label>
        <label>
          Search
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Reviewer, model, run"
          />
        </label>
      </div>
      {!sorted.length ? (
        <EmptyState
          title="No matching runs"
          message="Adjust filters or generate a deterministic baseline."
          command={emptyCommand}
        />
      ) : (
        <div className="table-scroll">
          <table className="data-table leaderboard-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Reviewer</th>
                <th
                  className={`numeric ${metric === "validated_case_rate" ? "selected-metric" : ""}`}
                  title="validated_case_rate"
                >
                  Validated rate
                </th>
                <th
                  className={`numeric ${metric === "detection_f_beta" ? "selected-metric" : ""}`}
                  title="detection_f_beta"
                >
                  Detection
                </th>
                <th className="numeric">Passes</th>
                <th
                  className={`numeric ${metric === "patch_apply_rate" ? "selected-metric" : ""}`}
                  title="patch_apply_rate"
                >
                  Patch
                </th>
                <th
                  className={`numeric ${metric === "test_pass_rate" ? "selected-metric" : ""}`}
                  title="test_pass_rate"
                >
                  Tests
                </th>
                <th
                  className={`numeric ${metric === "structural_pass_rate" ? "selected-metric" : ""}`}
                  title="structural_pass_rate"
                >
                  Validators
                </th>
                <th
                  className={`numeric ${metric === "false_positives_per_case" ? "selected-metric" : ""}`}
                  title="false_positives_per_case"
                >
                  False pos.
                </th>
                <th
                  className={`numeric ${metric === "latency_per_case_ms" ? "selected-metric" : ""}`}
                  title="latency_per_case_ms"
                >
                  Latency
                </th>
                <th>Status</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, index) => {
                const metrics = row.deterministic_metrics;
                const validated = metrics?.validated_case_rate ?? null;
                return (
                  <tr key={`${row.run_id}:${row.mode}`}>
                    <td className="numeric">{index + 1}</td>
                    <td>
                      <span className="reviewer-name">
                        <strong>{reviewerDisplayName(row)}</strong>
                      </span>
                      {showModel(row) ? (
                        <div className="table-subtitle">{row.model}</div>
                      ) : null}
                      {provenanceNote(row) ? (
                        <div className="table-subtitle">{provenanceNote(row)}</div>
                      ) : null}
                    </td>
                    <td className="numeric strong-metric metric-cell">
                      <span>{formatNumber(validated)}</span>
                      <span className="metric-bar">
                        <i
                          style={{
                            width: `${Math.max(0, Math.min(1, validated ?? 0)) * 100}%`,
                          }}
                        />
                      </span>
                    </td>
                    <td className="numeric secondary-metric">
                      {formatNumber(dynamicF(metrics, "detection", beta))}
                    </td>
                    <td className="numeric">
                      {row.deterministic_passes}/{row.case_count}
                    </td>
                    <td className="numeric">
                      {formatNumber(metrics?.patch_apply_rate)}
                    </td>
                    <td className="numeric">
                      {formatNumber(metrics?.test_pass_rate)}
                    </td>
                    <td className="numeric">
                      {formatNumber(metrics?.structural_pass_rate)}
                    </td>
                    <td className="numeric">{row.false_positives}</td>
                    <td className="numeric">
                      {formatLatency(
                        metrics?.latency_per_case_ms ??
                          row.latency_ms / Math.max(1, row.case_count),
                      )}
                    </td>
                    <td>{validationBadge(row)}</td>
                    <td>
                      {row.detail_available === false ? (
                        <Link href="/reports/audit-v1">Report</Link>
                      ) : (
                        <Link href={`/runs/${row.run_id}`}>View</Link>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="table-footnote">
        <code>validated_case_rate</code> counts only cases that reached
        deterministic validation.
        <code>detection_f_beta</code> only measures whether the seeded bug was
        found and localized.
      </p>
      {sorted.some((row) => row.source === "shipped-snapshot") ? (
        <p className="table-footnote">
          Rows marked <em>shipped audit result</em> come from the audit snapshot
          committed to this repository, not from your own runs.
        </p>
      ) : null}
      {sorted.some((row) => row.verified === false) ? (
        <p className="table-footnote">
          Rows marked <em>unverified</em> did not meet the default comparability
          policy (Docker-backed, full coverage, externally verified pack, exact
          reviewer output) and are shown here only because this view includes
          them.
        </p>
      ) : null}
      <p className="table-footnote">{CONTROL_BASELINE_NOTE}</p>
    </>
  );
}

function dynamicF(
  metrics: DeterministicMetrics | null | undefined,
  tier: "detection" | "validated",
  beta: number,
) {
  if (!metrics) return null;
  const precision =
    tier === "detection"
      ? metrics.detection_precision
      : metrics.validated_precision;
  const recall =
    tier === "detection" ? metrics.detection_recall : metrics.validated_recall;
  const weight = beta * beta;
  return precision + recall === 0
    ? 0
    : ((1 + weight) * precision * recall) / (weight * precision + recall);
}

function value(
  row: LeaderboardRow,
  metric: Metric,
  beta: number,
): number | null {
  const metrics = row.deterministic_metrics;
  if (!metrics) return null;
  if (metric === "detection_f_beta")
    return dynamicF(metrics, "detection", beta) ?? 0;
  if (metric === "latency_per_case_ms") return metrics.latency_per_case_ms;
  return metrics[metric];
}

// Where a row came from and whether it is comparable. Silence here is what let
// shipped audit numbers read as the viewer's own measurements.
function provenanceNote(row: LeaderboardRow): string | null {
  if (row.source === "shipped-snapshot")
    return "shipped audit result, not your run";
  if (row.verified === false) return "unverified: not comparable by default";
  return null;
}

function validationBadge(row: LeaderboardRow) {
  const metrics = row.deterministic_metrics;
  if (!metrics) return <StatusBadge tone="neutral">review only</StatusBadge>;
  if ((metrics.patch_apply_rate ?? 1) === 0)
    return <StatusBadge tone="danger">patch failed</StatusBadge>;
  if (metrics.detection_f_beta >= 0.8 && metrics.validated_case_rate <= 0.3)
    return <StatusBadge tone="warning">detected only</StatusBadge>;
  if (metrics.validated_case_rate >= 0.8)
    return <StatusBadge tone="success">validated</StatusBadge>;
  return <StatusBadge tone="danger">not validated</StatusBadge>;
}

// Show the raw model only when it adds information: control baselines already
// encode their model in the display name, and reference/custom rows have none.
function showModel(row: LeaderboardRow): boolean {
  return (
    !isControlBaseline(row) &&
    Boolean(row.model) &&
    row.reviewer !== "custom-command"
  );
}

function formatNumber(value: number | null | undefined) {
  return value == null ? "-" : value.toFixed(3);
}

function formatLatency(value: number | null | undefined) {
  return value == null ? "-" : `${Math.round(value)}ms`;
}
