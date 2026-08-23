import { EmptyState } from "../../components/EmptyState";
import { LeaderboardTable } from "../../components/LeaderboardTable";
import { PageHeader } from "../../components/PageHeader";
import { fetchJson, LeaderboardRow } from "../../lib/api";

const emptyCommand = `arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena serve`;

export default async function LeaderboardPage() {
  // include_unverified mirrors `arena leaderboard --include-unverified`: local runs
  // are trusted-local and would otherwise be filtered out server-side, leaving the
  // page empty. Rows carry their own `verified` flag so the table can mark which
  // ones fall short of the comparable-by-default policy.
  const rows = await fetchJson<LeaderboardRow[]>(
    "/leaderboard?include_unverified=true",
  ).catch(() => []);
  return (
    <>
      <PageHeader
        eyebrow="Results"
        title="Leaderboard"
        description="Your own runs, as recorded by the local API. Runs are ranked by validated_case_rate. Detection metrics are shown separately because a review can identify a bug without producing a valid fix."
      />
      {rows.length ? (
        <LeaderboardTable rows={rows} />
      ) : (
        // Deliberately NOT falling back to the committed audit snapshot: showing
        // shipped results here would read as the viewer's own measurements. The
        // published audit numbers have their own route, clearly labelled.
        <EmptyState
          title="No runs recorded yet"
          message="This page shows runs from your local API only. Execute a benchmark and start the server to populate it; the published audit results are at /reports/audit-v1."
          command={emptyCommand}
        />
      )}
    </>
  );
}
