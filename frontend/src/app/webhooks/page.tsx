"use client";
/** Outbound webhooks (CRUD, test, delivery log, retry) + inbound webhook URL. */
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import CodeBlock from "@/components/CodeBlock";
import Shell from "@/components/Shell";
import { api, apiBase } from "@/lib/api";
import type { Delivery, Webhook } from "@/lib/types";

const EVENTS = ["connector.online", "connector.offline", "task.completed", "skill.injected", "training.ready"];

interface Inbound { path: string; token: string; enabled_on_plan: boolean }

export default function WebhooksPage() {
  const [hooks, setHooks] = useState<Webhook[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [inbound, setInbound] = useState<Inbound | null>(null);
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["task.completed", "connector.offline"]);
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, d, i] = await Promise.all([api<Webhook[]>("/api/webhooks"), api<Delivery[]>("/api/webhooks/deliveries?limit=100"), api<Inbound>("/api/webhooks/inbound")]);
      setHooks(h);
      setDeliveries(d);
      setInbound(i);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const create = (e: FormEvent) => {
    e.preventDefault();
    act(async () => {
      await api("/api/webhooks", { method: "POST", json: { url, events } });
      setUrl("");
    });
  };
  const toggle = (ev: string) => setEvents((cur) => (cur.includes(ev) ? cur.filter((x) => x !== ev) : [...cur, ev]));
  const inboundUrl = inbound ? `${apiBase()}${inbound.path}` : "";
  const hookName = (id: string) => hooks.find((h) => h.id === id)?.url ?? id.slice(0, 8);

  return (
    <Shell>
      <h1 className="text-xl font-bold">🔔 Webhooks</h1>
      <p className="text-sm text-slate-400">Signed event notifications + an inbound URL to trigger tasks. <Link href="/docs/webhooks/" className="text-sky-300">Read the guide</Link>.</p>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300">{error} {error.includes("plan") && <Link href="/billing/" className="underline">Upgrade →</Link>}</p>}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="space-y-3">
          <form onSubmit={create} className="card space-y-3">
            <p className="font-semibold">Add outbound webhook</p>
            <input className="input" type="url" placeholder="https://hooks.zapier.com/…" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              {EVENTS.map((ev) => (
                <label key={ev} className={`cursor-pointer rounded-lg border px-2 py-1 font-mono text-xs ${events.includes(ev) ? "border-emerald-500 text-emerald-300" : "border-slate-700 text-slate-400"}`}>
                  <input type="checkbox" className="hidden" checked={events.includes(ev)} onChange={() => toggle(ev)} />{ev}
                </label>
              ))}
            </div>
            <button className="btn-primary w-full" disabled={!events.length}>Add webhook</button>
          </form>

          {hooks.map((h) => (
            <div key={h.id} className="card space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="truncate font-mono text-sm">{h.url}</p>
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${h.is_active ? "bg-emerald-900/40 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>{h.is_active ? "ACTIVE" : "PAUSED"}</span>
              </div>
              <p className="font-mono text-[11px] text-slate-400">{h.events_json.join(" · ")}</p>
              <p className="font-mono text-[11px] text-slate-500">secret: {reveal[h.id] ? h.secret : "whsec_••••••••"} <button className="text-sky-300" onClick={() => setReveal((r) => ({ ...r, [h.id]: !r[h.id] }))}>{reveal[h.id] ? "hide" : "show"}</button></p>
              <div className="flex flex-wrap gap-2 text-xs">
                <button className="btn-ghost px-2! py-1!" onClick={() => act(() => api(`/api/webhooks/${h.id}/test`, { method: "POST" }))}>Send test</button>
                <button className="btn-ghost px-2! py-1!" onClick={() => act(() => api(`/api/webhooks/${h.id}`, { method: "PATCH", json: { is_active: !h.is_active } }))}>{h.is_active ? "Pause" : "Resume"}</button>
                <button className="btn-ghost px-2! py-1!" onClick={() => act(() => api(`/api/webhooks/${h.id}/rotate-secret`, { method: "POST" }))}>Rotate secret</button>
                <button className="btn-ghost px-2! py-1! text-red-300" onClick={() => confirm("Delete webhook?") && act(() => api(`/api/webhooks/${h.id}`, { method: "DELETE" }))}>Delete</button>
              </div>
            </div>
          ))}
        </section>

        <section className="space-y-3">
          <div className="card space-y-3">
            <p className="font-semibold">Inbound webhook {inbound && !inbound.enabled_on_plan && <span className="ml-2 rounded-full bg-amber-900/50 px-2 py-0.5 text-[11px] text-amber-300">Pro+</span>}</p>
            <p className="text-xs text-slate-400">POST to this secret URL to run a command on your machine (Zapier, Make, n8n, cron…).</p>
            {inbound && <CodeBlock code={`curl -X POST "${inboundUrl}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"command":"echo hello","machine_name":"home-pc"}'`} />}
            {inbound && !inbound.enabled_on_plan && <p className="text-xs text-amber-300">Your plan doesn’t include inbound webhooks. <Link className="underline" href="/billing/?plan=PRO">Upgrade to Pro</Link>.</p>}
            <button className="btn-ghost text-xs" onClick={() => confirm("Rotate? The old URL stops working.") && act(() => api("/api/webhooks/inbound/rotate", { method: "POST" }))}>Rotate inbound URL</button>
          </div>

          <div className="card p-0">
            <div className="flex items-center justify-between p-3"><p className="font-semibold">Delivery log</p><button className="text-xs text-slate-400" onClick={load}>refresh</button></div>
            <div className="max-h-[60dvh] overflow-y-auto">
              {deliveries.length === 0 && <p className="p-3 text-sm text-slate-500">No deliveries yet.</p>}
              {deliveries.map((d) => (
                <div key={d.id} className="border-t border-slate-800 p-3 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-emerald-300">{d.event}</span>
                    <span className={d.status === "SUCCESS" ? "text-emerald-400" : d.status === "FAILED" ? "text-red-400" : "text-amber-300"}>{d.status} · {d.attempts} try · {d.response_code ?? "—"}</span>
                  </div>
                  <p className="truncate text-slate-500">{hookName(d.webhook_id)} · {new Date(d.created_at).toLocaleString()}</p>
                  {d.error && <p className="text-red-300">{d.error}</p>}
                  <div className="mt-1 flex gap-3">
                    <button className="text-sky-300" onClick={() => setOpen(open === d.id ? null : d.id)}>{open === d.id ? "hide" : "payload"}</button>
                    {d.status !== "SUCCESS" && <button className="text-sky-300" onClick={() => act(() => api(`/api/webhooks/deliveries/${d.id}/retry`, { method: "POST" }))}>retry now</button>}
                  </div>
                  {open === d.id && <pre className="mt-2 overflow-x-auto rounded bg-black/60 p-2 text-[11px]">{JSON.stringify(d.payload_json, null, 2)}{d.response_body ? `\n\nresponse: ${d.response_body}` : ""}</pre>}
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </Shell>
  );
}
