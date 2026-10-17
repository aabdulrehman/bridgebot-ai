"use client";
/** Authenticated app shell: top bar, token balance, mobile bottom nav. */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "@/lib/api";
import type { User } from "@/lib/types";

const NAV = [
  { href: "/dashboard/", label: "Control Room", icon: "🛰️" },
  { href: "/train/", label: "Train My Bot", icon: "🧠" },
];

export default function Shell({ children, balanceKey = 0 }: { children: ReactNode; balanceKey?: number }) {
  const router = useRouter();
  const path = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login/");
      return;
    }
    api<User>("/api/auth/me").then(setUser).catch(() => undefined);
  }, [router, balanceKey]);

  const logout = () => {
    setToken(null);
    router.replace("/login/");
  };

  return (
    <div className="mx-auto flex min-h-dvh max-w-6xl flex-col pb-20 md:pb-6">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/90 px-4 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon-192.png" alt="" className="h-8 w-8 rounded-lg" />
          <span className="font-bold tracking-tight">BridgeBot <span className="text-emerald-400">AI</span></span>
        </div>
        <nav className="hidden gap-1 md:flex">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}
              className={`rounded-lg px-3 py-1.5 text-sm ${path === n.href ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:text-slate-200"}`}>
              {n.icon} {n.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2 text-xs">
          {user && (
            <span className="rounded-full border border-emerald-700/50 bg-emerald-900/30 px-2 py-1 text-emerald-300" title={user.email}>
              🪙 {user.token_balance.toLocaleString()}
            </span>
          )}
          <button onClick={logout} className="btn-ghost px-2! py-1! text-xs">Logout</button>
        </div>
      </header>
      <main className="flex-1 space-y-4 p-4">{children}</main>
      <nav className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-2 border-t border-slate-800 bg-slate-950/95 pb-[env(safe-area-inset-bottom)] md:hidden">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href}
            className={`flex flex-col items-center py-2 text-xs ${path === n.href ? "text-emerald-300" : "text-slate-400"}`}>
            <span className="text-lg">{n.icon}</span>
            {n.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
