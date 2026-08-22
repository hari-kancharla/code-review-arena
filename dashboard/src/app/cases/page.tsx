import { CaseCatalog } from "../../components/CaseCatalog";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { CaseSummary, fetchJson } from "../../lib/api";

export default async function Cases() {
  // Every shipped pack. A pack missing from this list is invisible in the
  // catalogue even though it validates, certifies and runs in CI, so
  // tests/test_published_facts.py asserts this list against benchmark_sets/.
  const [v1, auditV1, auditV2, realfixSeedV0] = await Promise.all([
    fetchJson<CaseSummary[]>("/cases?benchmark_set=v1").catch(() => []),
    fetchJson<CaseSummary[]>("/cases?benchmark_set=audit_v1").catch(() => []),
    fetchJson<CaseSummary[]>("/cases?benchmark_set=audit_v2").catch(() => []),
    fetchJson<CaseSummary[]>("/cases?benchmark_set=realfix_seed_v0").catch(() => []),
  ]);
  const cases = [...auditV2, ...auditV1, ...v1, ...realfixSeedV0];
  return (
    <>
      <PageHeader
        eyebrow="Dataset"
        title="Benchmark cases"
        description="Browse seeded pull-request bugs, execution requirements, and structural validation used by each benchmark pack."
      />
      {cases.length === 0 ? (
        <EmptyState
          title="No cases to show"
          message="Cases are served by the API. Start it with `arena serve`, then reload this page."
          command={"arena serve"}
        />
      ) : (
        <CaseCatalog cases={cases} />
      )}
    </>
  );
}
