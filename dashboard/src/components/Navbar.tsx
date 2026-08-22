"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const links = [
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/reports/audit-v1", label: "Reports" },
  { href: "/cases", label: "Cases" },
  { href: "/methodology", label: "Methodology" },
  { href: "/docs", label: "Docs" },
];

export function Navbar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className={`topbar ${scrolled ? "is-scrolled" : ""}`}>
      <div className="topbar-inner">
        <Link className="brand" href="/" onClick={() => setOpen(false)}>Code Review Arena</Link>
        <button
          className="nav-toggle"
          type="button"
          aria-expanded={open}
          aria-controls="site-nav"
          onClick={() => setOpen((value) => !value)}
        >
          Menu
        </button>
        <nav className={`nav ${open ? "open" : ""}`} id="site-nav" aria-label="Primary">
          {links.map((link) => {
            const current = pathname ?? "/";
            const active = link.href === "/" ? current === "/" : current.startsWith(link.href);
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={active ? "active" : undefined}
                href={link.href}
                key={link.href}
                onClick={() => setOpen(false)}
              >
                {link.label}
              </Link>
            );
          })}
          <a href="https://github.com/hari-kancharla/code-review-arena">GitHub</a>
        </nav>
      </div>
    </header>
  );
}
