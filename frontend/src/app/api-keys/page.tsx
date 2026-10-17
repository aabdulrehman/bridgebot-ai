"use client";
/** Personal REST API keys. */
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import CodeBlock from "@/components/CodeBlock";
import Shell from "@/components/Shell";
import { api, apiBase } from "@/lib/api";
import type { ApiKey } from "@/lib/types";

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api<ApiKey[]>("/api/api-keys").then(setKeys).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const k = await api<ApiKey>("/api/api-keys", { method: "POST", json: { name } });
      setCreated(k);
      setName("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const revoke = async (id: string) => {
    if (!confirm("Revoke this key? Apps using it stop working immediately.")) return;
    await api(`/api/api-keys/${id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    load();
  };

  return (
    <Shell>
      <h1 className="text-xl font-bold">🔑 Personal API keys</h1>
      <p className="text-sm text-slate-400">Use the REST API from scripts, CI or no-code tools. See the <Link href="/docs/api/" className="text-sky-300">API reference</Link>.</p>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300">{error} {error.includes("plan") && <Link href="/billing/" className="underline">Upgrade →</Link>}</p>}
      <form onSubmit={create} className="card flex gap-2">
        <input className="input" placeholder="Key name, e.g. zapier" required value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-primary shrink-0">Create key</button>
      </form>
      {created?.api_key && (
        <div className="card space-y-2 border-amber-600/60">
          <p className="text-sm font-semibold text-amber-300">Copy your key now — it won’t be shown again.</p>
          <CodeBlock code={created.api_key} />
          <CodeBlock code={`curl "${apiBase()}/api/connectors" -H "Authorization: Bearer ${created.api_key}"`} />
          <button className="btn-ghost text-xs" onClick={() => setCreated(null)}>Done</button>
        </div>
      )}
      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-slate-900 text-left text-xs text-slate-400"><tr><th className="p-3">Name</th><th className="p-3">Prefix</th><th className="p-3">Created</th><th className="p-3">Last used</th><th className="p-3" /></tr></thead>
          <tbody>
            {keys.length === 0 && <tr><td className="p-3 text-slate-500" colSpan={5}>No keys yet.</td></tr>}
            {keys.map((k) => (
              <tr key={k.id} className={`border-t border-slate-800 ${k.revoked_at ? "opacity-50" : ""}`}>
                <td className="p-3">{k.name}</td><td className="p-3 font-mono text-xs">{k.prefix}…</td>
                <td className="p-3 text-xs">{new Date(k.created_at).toLocaleDateString()}</td>
                <td className="p-3 text-xs">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "never"}</td>
                <td className="p-3 text-right">{k.revoked_at ? <span className="text-xs">revoked</span> : <button className="text-xs text-red-400 hover:text-red-300" onClick={() => revoke(k.id)}>Revoke</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}
