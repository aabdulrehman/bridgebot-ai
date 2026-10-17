"use client";
/** Master Bot Marketplace — public catalogue; logged-in users add their bot to a master's group or clone it in one click. */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import MarketingLayout from "@/components/MarketingLayout";
import { api, getToken } from "@/lib/api";
import type { Bot, Master, Membership, User } from "@/lib/types";

const RANK: Record<string, number> = { FREE: 0, PRO: 1, BUSINESS: 2 };
const BADGE: Record<string, string> = { FREE: "bg-slate-800 text-slate-300", PRO: "bg-sky-900/60 text-sky-300", BUSINESS: "bg-amber-900/50 text-amber-300" };

export default function MarketplacePage() {
  const [masters, setMasters] = useState<Master[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [bots, setBots] = useState<Bot[]>([]);
  const [mems, setMems] = useState<Membership[]>([]);
  const [pick, setPick] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setMasters(await api<Master[]>("/api/marketplace")); } catch (e) { setError((e as Error).message); }
    if (!getToken()) return;
    try {
      setUser(await api<User>("/api/auth/me"));
      setBots(await api<Bot[]>("/api/bots"));
      setMems(await api<Membership[]>("/api/marketplace/me/memberships"));
    } catch { /* not logged in */ }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!mems.some((m) => !["GRADUATED", "FAILED", "WAITING"].includes(m.status))) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [mems, load]);

  const act = async (m: Master, kind: "join" | "make-master") => {
    const botId = pick[m.id] || bots[0]?.id;
    if (!botId) { setError("Link a bot first on the My Bots page."); return; }
    setBusy(m.id + kind); setError(null);
    try {
      await api(`/api/marketplace/${m.id}/${kind}`, { method: "POST", json: { bot_id: botId } });
      setFlash(kind === "make-master" ? `⚡ Cloning ${m.name} into your bot — welcome, skill injection and test run automatically.` : `➕ Bot added to ${m.name}'s group — onboarding started automatically.`);
      load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  };

  return (
    <MarketingLayout>
      <section className="mx-auto max-w-6xl space-y-6 px-4 py-12">
        <div className="space-y-2 text-center">
          <p className="text-sm font-semibold text-emerald-400">MASTER BOT MARKETPLACE</p>
          <h1 className="text-3xl font-extrabold md:text-4xl">Pick a master. Make your bot a master.</h1>
          <p className="mx-auto max-w-2xl text-slate-400">Add your bot to a Master Bot’s messenger group. The master welcomes it, injects its skill package (persona + prompts + tools), runs a follow-up test and certifies it — fully automatic.</p>
        </div>
        {flash && <p className="rounded-xl bg-emerald-950/50 p-3 text-sm text-emerald-300" onClick={() => setFlash(null)}>{flash}</p>}
        {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300" onClick={() => setError(null)}>{error} {/plan/i.test(error) && <Link href="/billing/" className="underline">Upgrade →</Link>}</p>}
        {user && bots.length === 0 && <p className="card text-sm">You have no linked bots yet. <Link href="/bots/" className="text-sky-300">Link a bot →</Link></p>}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {masters.map((m) => {
            const locked = user ? (RANK[user.plan_id] ?? 0) < (RANK[m.min_plan_id] ?? 0) : false;
            const mine = mems.filter((x) => x.master_id === m.id);
            return (
              <div key={m.id} className="card flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <span className="text-4xl">{m.avatar}</span>
                    <div><p className="font-bold">{m.name}</p><p className="text-xs text-slate-400">{m.tagline}</p></div>
                  </div>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${BADGE[m.min_plan_id] ?? BADGE.FREE}`}>{m.min_plan_name}+</span>
                </div>
                <p className="text-sm text-slate-300">{m.description}</p>
                <div className="flex flex-wrap gap-1">{m.tools.slice(0, 6).map((t) => <span key={t} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">{t}</span>)}</div>
                <p className="text-xs text-slate-500">👥 {m.members} bots in group · 🎓 {m.graduated} certified · skill <code>{m.skill_id}</code></p>
                {mine.map((x) => (
                  <p key={x.id} className="rounded-lg bg-slate-950 p-2 text-xs">🤖 {x.bot}: <b className={x.status === "GRADUATED" ? "text-emerald-300" : x.status === "FAILED" ? "text-red-300" : "text-amber-300"}>{x.status}</b>{x.master_clone ? " · master clone" : ""}{x.error ? ` — ${x.error}` : ""}</p>
                ))}
                <div className="mt-auto space-y-2">
                  {!user ? (
                    <Link href="/login/?mode=register" className="btn-primary w-full">Start free to use this master</Link>
                  ) : locked ? (
                    <Link href={`/billing/?plan=${m.min_plan_id}`} className="btn w-full border border-amber-600/60 text-amber-300">🔒 Requires {m.min_plan_name} — Upgrade</Link>
                  ) : (
                    <>
                      {bots.length > 1 && (
                        <select className="input" value={pick[m.id] ?? bots[0]?.id} onChange={(e) => setPick({ ...pick, [m.id]: e.target.value })}>
                          {bots.map((b) => <option key={b.id} value={b.id}>{b.name} ({b.framework})</option>)}
                        </select>
                      )}
                      <button className="btn-primary w-full" disabled={!!busy || bots.length === 0} onClick={() => act(m, "make-master")}>{busy === m.id + "make-master" ? "Cloning…" : "⚡ Make my bot a master"}</button>
                      <div className="grid grid-cols-2 gap-2">
                        <button className="btn-ghost text-xs" disabled={!!busy || bots.length === 0} onClick={() => act(m, "join")}>➕ Add bot to group</button>
                        {mine.length > 0 ? <Link className="btn-ghost text-xs" href={`/marketplace/group/?id=${m.id}`}>💬 Open group</Link> : <span />}
                      </div>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </MarketingLayout>
  );
}
