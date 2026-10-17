"use client";
/** My Bots — link any bot (Hermes, OpenClaw, n8n, LangChain, HTTP/CLI, Grok Bot…) via the Bot API or the local connector. */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import CodeBlock from "@/components/CodeBlock";
import Shell from "@/components/Shell";
import StatusDot from "@/components/StatusDot";
import { api, apiBase, getToken, wsBase } from "@/lib/api";
import { botSnippets } from "@/lib/botSnippets";
import type { Bot, BotMsg, Connector } from "@/lib/types";

const FRAMEWORKS: [string, string][] = [
  ["hermes", "Hermes Agent"], ["openclaw", "OpenClaw"], ["n8n", "n8n"], ["langchain", "LangChain / Python"],
  ["http", "Generic HTTP bot"], ["cli", "Any CLI bot"], ["grok", "External cloud bot (Grok Bot…)"], ["custom", "Custom"],
];
const DELIVERY: [Bot["delivery"], string][] = [
  ["poll", "Long-polling (works behind NAT — recommended)"], ["websocket", "WebSocket (realtime)"],
  ["webhook", "Webhook push to my URL"], ["connector", "Local connector (SKILL_INJECT + tasks)"],
];
const STATUS_COLOR: Record<string, string> = {
  GRADUATED: "text-emerald-300", TESTING: "text-sky-300", INJECTED: "text-sky-300", INJECTING: "text-amber-300",
  JOINED: "text-slate-300", WAITING: "text-amber-300", FAILED: "text-red-300",
};

export default function BotsPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [conns, setConns] = useState<Connector[]>([]);
  const [form, setForm] = useState({ name: "", framework: "hermes", delivery: "poll" as Bot["delivery"], webhook_url: "", connector_id: "", agent_name: "default" });
  const [created, setCreated] = useState<{ bot: Bot; token: string; secret?: string } | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [conv, setConv] = useState<BotMsg[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const convEnd = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setBots(await api<Bot[]>("/api/bots"));
      setConns(await api<Connector[]>("/api/connectors"));
    } catch (e) { setError((e as Error).message); }
  }, []);
  const loadConv = useCallback(async (id: string) => {
    try { setConv(await api<BotMsg[]>(`/api/bots/${id}/messages?limit=200`)); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!active) return;
    loadConv(active);
    const t = setInterval(() => loadConv(active), 4000);
    return () => clearInterval(t);
  }, [active, loadConv]);
  useEffect(() => { convEnd.current?.scrollIntoView({ block: "nearest" }); }, [conv.length]);

  // live events (BOT_MESSAGE, ONBOARDING_STATUS)
  useEffect(() => {
    const tok = getToken();
    if (!tok) return;
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${wsBase()}/ws/dashboard?token=${tok}`);
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "BOT_MESSAGE" && m.bot_id === active) setConv((c) => (c.some((x) => x.update_id === m.update_id) ? c : [...c, m]));
        if (m.type === "ONBOARDING_STATUS") load();
      };
    } catch { /* ignore */ }
    return () => ws?.close();
  }, [active, load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const body: Record<string, unknown> = { name: form.name, framework: form.framework, delivery: form.delivery, agent_name: form.agent_name || "default" };
      if (form.delivery === "webhook") body.webhook_url = form.webhook_url;
      if (form.delivery === "connector") body.connector_id = form.connector_id;
      const r = await api<{ bot: Bot; token: string; secret: string }>("/api/bots", { method: "POST", json: body });
      setCreated(r);
      setForm({ ...form, name: "" });
      load();
    } catch (err) { setError((err as Error).message); }
  };
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!active || !text.trim()) return;
    try {
      await api(`/api/bots/${active}/messages`, { method: "POST", json: { text } });
      setText("");
      loadConv(active);
    } catch (err) { setError((err as Error).message); }
  };
  const remove = async (b: Bot) => {
    if (!confirm(`Delete bot "${b.name}"? Its token stops working immediately.`)) return;
    await api(`/api/bots/${b.id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    if (active === b.id) setActive(null);
    load();
  };
  const rotate = async (b: Bot) => {
    if (!confirm("Rotate the token? The old one stops working.")) return;
    const r = await api<{ bot: Bot; token: string }>(`/api/bots/${b.id}/rotate-token`, { method: "POST" }).catch((e) => { setError(e.message); return null; });
    if (r) setCreated({ bot: r.bot, token: r.token });
  };
  const activeBot = bots.find((b) => b.id === active);

  return (
    <Shell>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">🤖 My Bots — Universal Bot Bridge</h1>
          <p className="text-sm text-slate-400">Link Hermes, OpenClaw, n8n, LangChain, any HTTP/CLI bot or an external cloud bot. Then train it in the <Link href="/marketplace/" className="text-sky-300">Marketplace</Link>. <Link href="/docs/bots/" className="text-sky-300">Docs →</Link></p>
        </div>
      </div>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300" onClick={() => setError(null)}>{error} {/plan/i.test(error) && <Link href="/billing/" className="underline">Upgrade →</Link>}</p>}

      <form onSubmit={create} className="card grid gap-3 md:grid-cols-2">
        <div><label className="label" htmlFor="bname">Bot name</label><input id="bname" className="input" required placeholder="e.g. hermes-support" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="label" htmlFor="fw">Framework</label>
          <select id="fw" className="input" value={form.framework} onChange={(e) => setForm({ ...form, framework: e.target.value })}>
            {FRAMEWORKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
        <div><label className="label" htmlFor="dl">Delivery</label>
          <select id="dl" className="input" value={form.delivery} onChange={(e) => setForm({ ...form, delivery: e.target.value as Bot["delivery"] })}>
            {DELIVERY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
        {form.delivery === "webhook" && <div><label className="label" htmlFor="wh">Bot webhook URL (https)</label><input id="wh" className="input" required placeholder="https://your-n8n.app/webhook/bot" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} /></div>}
        {form.delivery === "connector" && (
          <>
            <div><label className="label" htmlFor="cn">Machine</label>
              <select id="cn" className="input" required value={form.connector_id} onChange={(e) => setForm({ ...form, connector_id: e.target.value })}>
                <option value="">Select a machine…</option>
                {conns.map((c) => <option key={c.id} value={c.id}>{c.machine_name} ({c.status})</option>)}
              </select></div>
            <div><label className="label" htmlFor="an">Agent name on that machine</label><input id="an" className="input" value={form.agent_name} onChange={(e) => setForm({ ...form, agent_name: e.target.value })} /></div>
          </>
        )}
        <div className="md:col-span-2"><button className="btn-primary">Link bot</button></div>
      </form>

      {created && (
        <div className="card space-y-3 border-amber-600/60">
          <p className="text-sm font-semibold text-amber-300">“{created.bot.name}” linked. Copy the bot token now — it won’t be shown again.</p>
          <CodeBlock code={created.token} />
          {botSnippets(apiBase(), created.token, created.bot.framework, created.secret).map((s) => (
            <div key={s.title}><p className="label">{s.title}</p><CodeBlock code={s.code} /></div>
          ))}
          <button className="btn-ghost text-xs" onClick={() => setCreated(null)}>Done</button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-3">
          {bots.length === 0 && <p className="card text-sm text-slate-500">No bots linked yet.</p>}
          {bots.map((b) => (
            <div key={b.id} className={`card space-y-2 ${active === b.id ? "border-emerald-600/60" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold"><StatusDot online={b.online} /> {b.name}</p>
                  <p className="text-xs text-slate-500">{b.framework} · {b.delivery} · {b.token_prefix}… {b.last_seen_at ? `· seen ${new Date(b.last_seen_at).toLocaleTimeString()}` : ""}</p>
                </div>
                <div className="flex gap-1 text-xs">
                  {b.delivery !== "connector" && <button className="btn-ghost px-2! py-1!" onClick={() => setActive(b.id)}>💬 Chat</button>}
                  <button className="btn-ghost px-2! py-1!" onClick={() => rotate(b)}>↻ Token</button>
                  <button className="btn-ghost px-2! py-1! text-red-300" onClick={() => remove(b)}>✕</button>
                </div>
              </div>
              {b.persona && <p className="rounded-lg bg-slate-950 p-2 text-xs text-slate-400">🎭 Master persona: {b.persona.slice(0, 160)}{b.persona.length > 160 ? "…" : ""}</p>}
              <div className="flex flex-wrap gap-1">
                {b.skills.map((s) => <span key={s.skill_id} className="rounded-full bg-emerald-950/60 px-2 py-0.5 text-[11px] text-emerald-300">🧠 {s.name ?? s.skill_id}</span>)}
              </div>
              {(b.groups ?? []).map((g) => (
                <p key={g.master_id} className="text-xs">
                  <Link href={`/marketplace/group/?id=${g.master_id}`} className="text-sky-300">{g.master}</Link>{" "}
                  <span className={STATUS_COLOR[g.status] ?? ""}>{g.status}{g.master_clone ? " · master clone" : ""}</span>
                  {g.error && <span className="text-slate-500"> — {g.error}</span>}
                </p>
              ))}
            </div>
          ))}
        </div>
        <div className="card flex min-h-[420px] flex-col">
          {activeBot ? (
            <>
              <p className="mb-2 font-semibold">💬 {activeBot.name} <span className="text-xs text-slate-500">({activeBot.delivery})</span></p>
              <div className="flex-1 space-y-2 overflow-y-auto rounded-xl bg-slate-950 p-3 text-sm" style={{ maxHeight: 480 }}>
                {conv.length === 0 && <p className="text-slate-500">No messages yet. Say hi — your bot receives it via {activeBot.delivery}.</p>}
                {conv.map((m) => (
                  <div key={`${m.direction}${m.update_id}`} className={`max-w-[85%] rounded-xl px-3 py-2 ${m.direction === "out" ? "ml-auto bg-emerald-900/40" : "bg-slate-800"}`}>
                    <p className="text-[10px] text-slate-400">{m.direction === "out" ? "🤖 bot" : m.sender} · {m.kind}{m.chat === "group" ? " · group" : ""}</p>
                    <p className="whitespace-pre-wrap break-words">{m.text}</p>
                  </div>
                ))}
                <div ref={convEnd} />
              </div>
              <form onSubmit={send} className="mt-2 flex gap-2">
                <input className="input" placeholder="Message your bot…" value={text} onChange={(e) => setText(e.target.value)} />
                <button className="btn-primary shrink-0">Send</button>
              </form>
            </>
          ) : <p className="m-auto text-sm text-slate-500">Select “Chat” on a bot to talk to it.</p>}
        </div>
      </div>
    </Shell>
  );
}
