"use client";
/** Docs shell: marketing nav + sidebar + prose area. */
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import MarketingLayout from "./MarketingLayout";

const PAGES = [
  { href: "/docs/", label: "Getting started" },
  { href: "/docs/connect/", label: "Connect your machine" },
  { href: "/docs/api/", label: "API reference" },
  { href: "/docs/webhooks/", label: "Webhooks" },
];

export default function DocsLayout({ title, children }: { title: string; children: ReactNode }) {
  const path = usePathname();
  return (
    <MarketingLayout>
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-10 md:grid-cols-[220px_1fr]">
        <aside className="md:sticky md:top-20 md:self-start">
          <p className="label">Documentation</p>
          <nav className="flex gap-2 overflow-x-auto md:flex-col md:gap-1">
            {PAGES.map((p) => (
              <Link key={p.href} href={p.href}
                className={`shrink-0 rounded-lg px-3 py-1.5 text-sm ${path === p.href ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:text-white"}`}>{p.label}</Link>
            ))}
          </nav>
        </aside>
        <article className="docs min-w-0 space-y-4">
          <h1 className="text-3xl font-extrabold">{title}</h1>
          {children}
        </article>
      </div>
    </MarketingLayout>
  );
}
