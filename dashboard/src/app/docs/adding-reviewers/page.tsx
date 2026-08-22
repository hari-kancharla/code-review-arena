import { CodeBlock } from "../../../components/CodeBlock";
import { DocsLayout } from "../../../components/DocsLayout";
import { PageHeader } from "../../../components/PageHeader";

// Kept in step with the harness parser by tests/test_published_facts.py, which
// parses this exact text and requires an "exact" outcome. An example the parser
// rejects costs a reviewer author the invalid-output penalty on every case.
const schema = `{
  "findings": [{
    "title": "specific production bug",
    "summary": "what is wrong and why it matters",
    "category": "correctness",
    "severity": "high",
    "file": "path/to/file.py",
    "line_start": 1,
    "line_end": 4,
    "evidence": "the code or behaviour that shows it",
    "confidence": 0.91,
    "suggested_fix": "natural language repair",
    "suggested_patch": "diff --git ...",
    "patch_confidence": 0.88
  }],
  "overall_risk": "high",
  "review_summary": "short summary"
}`;

export default function AddingReviewersPage() {
  return (
    <>
      <PageHeader eyebrow="Docs" title="Adding Reviewers" description="Connect a reviewer while preserving deterministic output parsing." />
      <DocsLayout>
        <h1>Reviewer interface</h1>
        <p>Implement <code>BaseReviewer.review(case_context) -&gt; ReviewerResponse</code> and register the adapter. The reviewer receives the diff and contextual evidence, never ground truth.</p>
        <h2>Patch-aware response</h2>
        <p>Responses must be valid JSON without Markdown fences. Natural-language fixes remain supported, but patch/full mode only validates repair outcomes when a unified patch is supplied.</p>
        <CodeBlock compact>{schema}</CodeBlock>
      </DocsLayout>
    </>
  );
}
