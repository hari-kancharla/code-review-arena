import Link from "next/link";

export function Footer() {
  return (
    <footer className="site-footer">
      <div>
        <strong>Code Review Arena</strong>
        <p>Local execution-backed benchmark for code-review agents.</p>
      </div>
      <nav aria-label="Footer">
        <Link href="/leaderboard">Leaderboard</Link>
        <Link href="/cases">Benchmark packs</Link>
        <Link href="/methodology">Methodology</Link>
        <Link href="/docs">Docs</Link>
        <a href="https://github.com/hari-kancharla/code-review-arena">GitHub</a>
      </nav>
    </footer>
  );
}
