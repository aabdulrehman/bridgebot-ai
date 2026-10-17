"use client";
/** HTTP tunnels: public https://…/t/{slug}/ URLs proxied over the connector WebSocket to a local port. */
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import CodeBlock from "@/components/CodeBlock";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import type { Connector, Tunnel } from "@/lib/types";

const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export default function TunnelsPage() {
  const [tunnels, setTunnels] = useState<Tunnel[]>([]);
  const [machines, setMachines] = useState<Connector[]>([]);
  const [connectorId, setConnectorId] = useState("");
  const [port, setPort] = useState("5678");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [protect, setProtect] = useState(false);
  const [secret, setSecret] = useState<{ url: string; token: string } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, m] = await Promise.all([api<Tunnel[]>("/api/tunnels"), api<Connector[]>("/api/connectors")]);
      setTunnels(t);
      setMachines(m);
      setConnectorId((cur) => cur || m[0]?.id || "");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
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
  const showToken = (r: { tunnel: Tunnel; access_token: string | null }) => r.access_token && setSecret({ url: r.tunnel.public_url, token: r.access_token });
  const create = (e: FormEvent) => {
    e.preventDefault();
    act(async () => {
      const r = await api<{ tunnel: Tunnel; access_token: string | null }>("/api/tunnels", {
        method: "POST", json: { connector_id: connectorId, local_port: Number(port), name: name || undefined, slug: slug || undefined, require_token: protect },
      });
      showToken(r);
      setName("");
      setSlug("");
    });
  };
  const copy = (text: string) => {
    navigator.clipboard?.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <Shell>
      <h1 className="text-xl font-bold">🌐 HTTP Tunnels</h1>
      <p className="text-sm text-slate-400">
        Give any local web app a public HTTPS URL — n8n webhooks, Ollama, a dev server, Home Assistant. Requests travel over your connector’s WebSocket, so no port-forwarding or ngrok needed.{" "}
        <Link href="/docs/tunnels/" className="text-sky-300">Read the guide</Link>.
      </p>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300">{error} {(error.includes("plan") || error.includes("limit")) && <Link href="/billing/" className="underline">Upgrade →</Link>}</p>}

      {secret && (
        <div className="card space-y-2 border-amber-600/60">
          <p className="font-semibold text-amber-300">Access token — shown once, copy it now</p>
          <CodeBlock code={secret.token} />
          <CodeBlock code={`curl -H "X-Tunnel-Token: ${secret.token}" ${secret.url}\n# or\ncurl "${secret.url}?tunnel_token=${secret.token}"`} />
          <button className="btn-ghost text-xs" onClick={() => setSecret(null)}>I saved it</button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <form onSubmit={create} className="card h-fit space-y-3">
          <p className="font-semibold">New tunnel</p>
          {machines.length === 0 ? (
            <p className="text-sm text-slate-400">Connect a machine first on the <Link className="text-sky-300" href="/dashboard/">Control Room</Link>.</p>
          ) : (
            <>
              <label className="block text-xs text-slate-400">Machine
                <select className="input mt-1" value={connectorId} onChange={(e) => setConnectorId(e.target.value)}>
                  {machines.map((m) => <option key={m.id} value={m.id}>{m.machine_name} {m.status === "ONLINE" ? "🟢" : "⚪"}</option>)}
                </select>
              </label>
              <label className="block text-xs text-slate-400">Local port (on that machine, 127.0.0.1)
                <input className="input mt-1" type="number" min={1} max={65535} required value={port} onChange={(e) => setPort(e.target.value)} />
              </label>
              <label className="block text-xs text-slate-400">Name (optional)
                <input className="input mt-1" placeholder="n8n" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="block text-xs text-slate-400">Custom slug (optional, 4–48 chars a-z 0-9 -)
                <input className="input mt-1 font-mono" placeholder="my-n8n" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
              </label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={protect} onChange={(e) => setProtect(e.target.checked)} /> Protect with an access token</label>
              <button className="btn-primary w-full">Create tunnel</button>
              <p className="text-[11px] text-slate-500">Free: 1 tunnel · Pro: 5 · Business: 20. Connector v1.1.0+ required.</p>
            </>
          )}
        </form>

        <section className="space-y-3">
          {tunnels.length === 0 && <div className="card text-sm text-slate-400">No tunnels yet. Start <code>python -m http.server 8000</code> on your machine and create a tunnel to port 8000 to try it.</div>}
          {tunnels.map((t) => (
            <div key={t.id} className="card space-y-2" data-tunnel={t.slug}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-semibold">{t.name || t.slug} <span className="font-mono text-xs text-slate-400">→ {t.machine_name ?? "machine"}:{t.local_port}</span></p>
                <div className="flex gap-1 text-[11px]">
                  {t.protected && <span className="rounded-full bg-amber-900/40 px-2 py-0.5 text-amber-300">🔒 token</span>}
                  <span className={`rounded-full px-2 py-0.5 ${!t.is_active ? "bg-slate-800 text-slate-400" : t.online ? "bg-emerald-900/40 text-emerald-300" : "bg-red-950/60 text-red-300"}`}>
                    {!t.is_active ? "PAUSED" : t.online ? "ONLINE" : "MACHINE OFFLINE"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 rounded-lg bg-slate-950 p-2">
                <a href={t.public_url} target="_blank" rel="noopener noreferrer" className="flex-1 truncate font-mono text-xs text-sky-300">{t.public_url}</a>
                <button className="btn-ghost px-2! py-1! text-xs" onClick={() => copy(t.public_url)}>{copied === t.public_url ? "Copied ✓" : "Copy"}</button>
              </div>
              <p className="text-[11px] text-slate-500">
                {t.requests_count} requests · ↑ {fmtBytes(t.bytes_in)} · ↓ {fmtBytes(t.bytes_out)} · last status {t.last_status ?? "—"} · {t.last_used_at ? `used ${new Date(t.last_used_at).toLocaleString()}` : "never used"}
              </p>
              <div className="flex flex-wrap gap-2 text-xs">
                <button className="btn-ghost px-2! py-1!" onClick={() => act(() => api(`/api/tunnels/${t.id}`, { method: "PATCH", json: { is_active: !t.is_active } }))}>{t.is_active ? "Pause" : "Resume"}</button>
                {t.protected ? (
                  <>
                    <button className="btn-ghost px-2! py-1!" onClick={() => act(async () => showToken(await api(`/api/tunnels/${t.id}/rotate-token`, { method: "POST" })))}>Rotate token</button>
                    <button className="btn-ghost px-2! py-1!" onClick={() => confirm("Make this tunnel public (no token)?") && act(() => api(`/api/tunnels/${t.id}`, { method: "PATCH", json: { require_token: false } }))}>Remove token</button>
                  </>
                ) : (
                  <button className="btn-ghost px-2! py-1!" onClick={() => act(async () => showToken(await api(`/api/tunnels/${t.id}`, { method: "PATCH", json: { require_token: true } })))}>Add token</button>
                )}
                <button className="btn-ghost px-2! py-1!" onClick={() => {
                  const v = prompt("New local port", String(t.local_port));
                  if (v) act(() => api(`/api/tunnels/${t.id}`, { method: "PATCH", json: { local_port: Number(v) } }));
                }}>Change port</button>
                <button className="btn-ghost px-2! py-1! text-red-300" onClick={() => confirm("Delete tunnel? The URL stops working.") && act(() => api(`/api/tunnels/${t.id}`, { method: "DELETE" }))}>Delete</button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </Shell>
  );
}
