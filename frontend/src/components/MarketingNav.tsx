"use client";
/** Public site navbar (landing, pricing, docs). */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";

const LINKS = [
  { href: "/#features", label: "Features" },
  { href: "/marketplace/", label: "Marketplace" },
  { href: "/pricing/", label: "Pricing" },
  { href: "/docs/", label: "Docs" },
  { href: "/docs/bots/", label: "Bot API" },
];

export default function MarketingNav() {
  const path = usePathname();
  const [authed, setAuthed] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => setAuthed(!!getToken()), []);
  return (
    <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon-192.png" alt="" className="h-8 w-8 rounded-lg" />
          BridgeBot <span className="text-emerald-400">AI</span>
        </Link>
        <nav className="hidden items-center gap-6 text-sm md:flex">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className={`hover:text-white ${path === l.href ? "text-emerald-300" : "text-slate-400"}`}>{l.label}</Link>
          ))}
        </nav>
        <div className="hidden items-center gap-2 md:flex">
          {authed ? (
            <Link href="/dashboard/" className="btn-primary">Open dashboard →</Link>
          ) : (
            <>
              <Link href="/login/" className="btn-ghost">Log in</Link>
              <Link href="/login/?mode=register" className="btn-primary">Start free</Link>
            </>
          )}
        </div>
        <button className="btn-ghost px-3! md:hidden" onClick={() => setOpen((o) => !o)} aria-label="Menu">☰</button>
      </div>
      {open && (
        <nav className="space-y-1 border-t border-slate-800 px-4 py-3 md:hidden">
          {LINKS.map((l) => <Link key={l.href} href={l.href} onClick={() => setOpen(false)} className="block py-2 text-slate-300">{l.label}</Link>)}
          <Link href={authed ? "/dashboard/" : "/login/?mode=register"} className="btn-primary mt-2 w-full">{authed ? "Open dashboard" : "Start free"}</Link>
        </nav>
      )}
    </header>
  );
}
