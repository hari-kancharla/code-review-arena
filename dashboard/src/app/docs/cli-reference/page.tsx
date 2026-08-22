import { CodeBlock } from "../../../components/CodeBlock";
import { DocsLayout } from "../../../components/DocsLayout";
import { PageHeader } from "../../../components/PageHeader";

export default function CliReferencePage() {
  return (
    <>
      <PageHeader eyebrow="Docs" title="CLI Reference" description="Core commands for local benchmark execution and reporting." />
      <DocsLayout>
        <h1>CLI reference</h1>
        <h2>Validate a benchmark pack</h2>
        <CodeBlock compact>{`arena validate benchmark_sets/v1
arena validate benchmark_sets/audit_v1
arena validate benchmark_sets/audit_v2`}</CodeBlock>
        <h2>Run deterministic controls</h2>
        <CodeBlock compact>{`arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:keyword_gamer --mode full --allow-local-execution
arena run benchmark_sets/audit_v2 --reviewer shallow-patch --mode full --allow-local-execution`}</CodeBlock>
        <h2>Rank runs</h2>
        {/* --include-unverified matters here: every run above uses
            --allow-local-execution, so all of them are trusted-local and the
            default leaderboard filters them out, printing an empty table. */}
        <CodeBlock compact>arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified</CodeBlock>
        <h2>Generate the audit report</h2>
        <CodeBlock compact>arena audit-report runs/ --output docs/reports/audit-v1-results.md</CodeBlock>
        <h2>Serve the API</h2>
        <CodeBlock compact>arena serve</CodeBlock>
      </DocsLayout>
    </>
  );
}
