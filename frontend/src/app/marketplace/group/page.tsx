"use client";
/** A Master Bot's messenger group: live feed + members + onboarding status. */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import type { Master, Membership } from "@/lib/types";

interface Feed { master: Master; members: Membership[]; messages: { id: number; sender_type: string; sender: string; text: string; created_at: string }[] }
const STEPS = ["JOINED", "INJECTING", "INJECTED", "TESTING", "GRADUATED"];

export default function GroupPage() {
  const [id, setId] = useState<string | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { setId(new URLSearchParams(window.location.search).get("id")); }, []);
  const load = useCallback(async () => {
    if (!id) return;
    try { setFeed(await api<Feed>(`/api/marketplace/${id}/group?limit=300`)); } catch (e) { setError((e as Error).message); }
  }, [id]);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [feed?.messages.length]);

  return (
    <Shell>
      <Link href="/marketplace/" className="text-xs text-sky-300">← Marketplace</Link>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300">{error}</p>}
      {feed && (
        <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
          <div className="card flex flex-col">
            <p className="mb-2 font-bold">{feed.master.avatar} {feed.master.name} <span className="text-xs font-normal text-slate-500">group · {feed.members.length} bots</span></p>
            <div className="flex-1 space-y-2 overflow-y-auto rounded-xl bg-slate-950 p-3 text-sm" style={{ maxHeight: 560 }}>
              {feed.messages.map((m) => (
                <div key={m.id} className={`rounded-xl px-3 py-2 ${m.sender_type === "master" ? "bg-emerald-950/50" : m.sender_type === "system" ? "bg-slate-900 text-xs text-slate-400" : "bg-slate-800"}`}>
                  <p className="text-[10px] text-slate-400">{m.sender_type === "master" ? "👑 " : m.sender_type === "bot" ? "🤖 " : ""}{m.sender} · {new Date(m.created_at).toLocaleTimeString()}</p>
                  <p className="whitespace-pre-wrap break-words">{m.text}</p>
                </div>
              ))}
              <div ref={end} />
            </div>
          </div>
          <div className="space-y-3">
            <div className="card space-y-1 text-sm">
              <p className="label">Persona</p><p className="text-slate-300">{feed.master.persona}</p>
              <p className="label pt-2">Follow-up test</p><p className="text-slate-400">{feed.master.test_prompt}</p>
            </div>
            {feed.members.map((m) => {
              const idx = STEPS.indexOf(m.status);
              return (
                <div key={m.id} className={`card space-y-2 ${m.mine ? "border-emerald-700/50" : ""}`}>
                  <p className="text-sm font-semibold">🤖 {m.bot} <span className="text-xs text-slate-500">{m.framework}{m.mine ? " · yours" : ""}{m.master_clone ? " · master clone" : ""}</span></p>
                  <div className="flex gap-1">
                    {STEPS.map((s, i) => <span key={s} title={s} className={`h-1.5 flex-1 rounded-full ${m.status === "FAILED" ? "bg-red-700" : i <= idx ? "bg-emerald-500" : "bg-slate-700"}`} />)}
                  </div>
                  <p className="text-xs text-slate-400">{m.status}{m.error ? ` — ${m.error}` : ""}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Shell>
  );
}
