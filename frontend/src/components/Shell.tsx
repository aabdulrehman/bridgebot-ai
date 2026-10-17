"use client";
/** Authenticated app shell: top bar (plan + tokens), side/bottom nav, admin link, footer. */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, api, getToken, setToken } from "@/lib/api";
import { cacheMe, upgradeMessage, waLink } from "@/lib/site";
import type { User } from "@/lib/types";
import Footer from "./Footer";

const NAV = [
  { href: "/dashboard/", label: "Control Room", short: "Control", icon: "🛰️" },
  { href: "/train/", label: "Train My Bot", short: "Train", icon: "🧠" },
  { href: "/webhooks/", label: "Webhooks", short: "Hooks", icon: "🔔" },
  { href: "/api-keys/", label: "API Keys", short: "Keys", icon: "🔑" },
  { href: "/billing/", label: "Subscription", short: "Plan", icon: "💳" },
];

export default function Shell({ children, balanceKey = 0, adminOnly = false }: { children: ReactNode; balanceKey?: number; adminOnly?: boolean }) {
  const router = useRouter();
  const path = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login/");
      return;
    }
    api<User>("/api/auth/me")
      .then((u) => {
        setUser(u);
        cacheMe({ email: u.email, plan_id: u.plan_id, plan_name: u.plan?.name, role: u.role });
        if (adminOnly && u.role !== "admin") router.replace("/dashboard/");
      })
      .catch((e: ApiError) => {
        if (e.status === 403) setBlocked(e.message);
      });
  }, [router, balanceKey, adminOnly]);

  const logout = () => {
    setToken(null);
    cacheMe(null);
    router.replace("/login/");
  };

  if (blocked) {
    return (
      <main className="grid min-h-dvh place-items-center p-4">
        <div className="card max-w-md space-y-4 text-center">
          <p className="text-4xl">⛔</p>
          <p className="font-semibold">{blocked}</p>
          <a className="btn-primary" href={waLink("Hello BridgeBot AI, my account is blocked. Can you help?")} target="_blank" rel="noopener noreferrer">Contact support on WhatsApp</a>
          <button className="btn-ghost w-full" onClick={logout}>Logout</button>
        </div>
      </main>
    );
  }

  const nav = user?.role === "admin" ? [...NAV, { href: "/admin/", label: "Master Admin", short: "Admin", icon: "🛡️" }] : NAV;

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3">
          <Link href="/" className="flex items-center gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/icon-192.png" alt="" className="h-8 w-8 rounded-lg" />
            <span className="hidden font-bold tracking-tight sm:inline">BridgeBot <span className="text-emerald-400">AI</span></span>
          </Link>
          <nav className="hidden gap-1 lg:flex">
            {nav.map((n) => (
              <Link key={n.href} href={n.href}
                className={`rounded-lg px-3 py-1.5 text-sm ${path === n.href ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:text-slate-200"}`}>
                {n.icon} {n.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-2 text-xs">
            {user && (
              <>
                <Link href="/billing/" className="rounded-full border border-sky-700/50 bg-sky-900/30 px-2 py-1 text-sky-300" title="Your plan">
                  {user.plan?.name ?? user.plan_id}{user.subscription_status === "PENDING_UPGRADE" ? " ⏳" : ""}
                </Link>
                <span className="rounded-full border border-emerald-700/50 bg-emerald-900/30 px-2 py-1 text-emerald-300" title={`${user.email} · platform tokens`}>
                  🪙 {user.token_balance.toLocaleString()}
                </span>
              </>
            )}
            <button onClick={logout} className="btn-ghost px-2! py-1! text-xs">Logout</button>
          </div>
        </div>
      </header>
      {user?.subscription_status === "PAST_DUE" && (
        <div className="bg-amber-900/40 px-4 py-2 text-center text-xs text-amber-200">
          Your paid period ended and you were moved to Free. <Link href="/billing/" className="underline">Renew</Link> ·{" "}
          <a className="underline" href={waLink(upgradeMessage("Pro", user.email))} target="_blank" rel="noopener noreferrer">WhatsApp</a>
        </div>
      )}
      <main className="mx-auto w-full max-w-7xl flex-1 space-y-4 p-4 pb-24 lg:pb-6">{children}</main>
      <div className="pb-16 lg:pb-0"><Footer /></div>
      <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t border-slate-800 bg-slate-950/95 pb-[env(safe-area-inset-bottom)] lg:hidden">
        {nav.map((n) => (
          <Link key={n.href} href={n.href}
            className={`flex flex-1 flex-col items-center py-2 text-[10px] ${path === n.href ? "text-emerald-300" : "text-slate-400"}`}>
            <span className="text-lg">{n.icon}</span>
            {n.short}
          </Link>
        ))}
      </nav>
    </div>
  );
}
