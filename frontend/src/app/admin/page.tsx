"use client";
/** Master Admin dashboard (role=admin). */
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import StatusDot from "@/components/StatusDot";
import AdminMarketplace from "@/components/AdminMarketplace";
import { api } from "@/lib/api";
import { money } from "@/lib/site";
import type { UpgradeRequest, User } from "@/lib/types";

type Tab = "overview" | "upgrades" | "users" | "machines" | "marketplace" | "payments" | "audit";
interface Stats {
  users_total: number; users_by_plan: Record<string, number>; users_by_status: Record<string, number>; new_users_7d: number;
  connectors_total: number; connectors_online: number; dashboards_open: number; mrr_cents: number; revenue_total_cents: number;
  revenue_30d_cents: number; pending_upgrades: number; tasks_today: number; tokens_used_period: number;
  trainings_by_status: Record<string, number>; webhook_deliveries: Record<string, number>;
}
type AdminUser = User & { machines: number; machines_online: number };
interface AdminConnector { id: string; machine_name: string; user_email: string; online: boolean; last_heartbeat: string | null; created_at: string }
interface PaymentRow { id: string; user_email: string; plan_id: string; amount_cents: number; currency: string; provider: string; reference: string | null; created_at: string }
interface AuditRow { id: string; connector_id: string | null; event_type: string; payload_json: unknown; created_at: string }

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return <div className="card"><p className="label">{label}</p><p className="text-2xl font-bold">{value}</p>{sub && <p className="text-xs text-slate-500">{sub}</p>}</div>;
}

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<Stats | null>(null);
  const [reqs, setReqs] = useState<UpgradeRequest[]>([]);
  const [reqFilter, setReqFilter] = useState("PENDING");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [conns, setConns] = useState<AdminConnector[]>([]);
  const [pays, setPays] = useState<PaymentRow[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [auditFilter, setAuditFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStats(await api<Stats>("/api/admin/stats"));
      if (tab === "upgrades") setReqs(await api<UpgradeRequest[]>(`/api/admin/upgrade-requests?status=${reqFilter}`));
      if (tab === "users") setUsers((await api<{ users: AdminUser[] }>(`/api/admin/users?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`)).users);
      if (tab === "machines") setConns(await api<AdminConnector[]>("/api/admin/connectors"));
      if (tab === "payments") setPays(await api<PaymentRow[]>("/api/admin/payments"));
      if (tab === "audit") setAudit(await api<AuditRow[]>(`/api/admin/audit?limit=300${auditFilter ? `&event_type=${encodeURIComponent(auditFilter)}` : ""}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [tab, reqFilter, q, auditFilter]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    try {
      await fn();
      setFlash(ok);
      setTimeout(() => setFlash(null), 2500);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const patchUser = (id: string, body: Record<string, unknown>, ok: string) => act(() => api(`/api/admin/users/${id}`, { method: "PATCH", json: body }), ok);

  const TABS: [Tab, string][] = [["overview", "Overview"], ["upgrades", `Upgrades${stats?.pending_upgrades ? ` (${stats.pending_upgrades})` : ""}`], ["users", "Users"], ["machines", "Machines"], ["marketplace", "Marketplace"], ["payments", "Revenue"], ["audit", "Audit"]];

  return (
    <Shell adminOnly>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">🛡️ Master Admin</h1>
        <div className="flex gap-1 overflow-x-auto rounded-xl bg-slate-900 p-1 text-sm">
          {TABS.map(([t, l]) => (
            <button key={t} onClick={() => setTab(t)} className={`shrink-0 rounded-lg px-3 py-1 ${tab === t ? "bg-slate-800 text-emerald-300" : "text-slate-400"}`}>{l}</button>
          ))}
        </div>
      </div>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300" onClick={() => setError(null)}>{error}</p>}
      {flash && <p className="rounded-xl bg-emerald-950/50 p-3 text-sm text-emerald-300">{flash}</p>}

      {tab === "overview" && stats && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Users" value={stats.users_total} sub={`+${stats.new_users_7d} this week`} />
            <Stat label="MRR" value={money(stats.mrr_cents)} sub="active paid plans" />
            <Stat label="Revenue (30d)" value={money(stats.revenue_30d_cents)} sub={`all-time ${money(stats.revenue_total_cents)}`} />
            <Stat label="Pending upgrades" value={stats.pending_upgrades} />
            <Stat label="Machines online" value={`${stats.connectors_online}/${stats.connectors_total}`} sub={`${stats.dashboards_open} dashboards open`} />
            <Stat label="Tasks today" value={stats.tasks_today} />
            <Stat label="Tokens used (period)" value={stats.tokens_used_period.toLocaleString()} />
            <Stat label="Webhook deliveries" value={Object.values(stats.webhook_deliveries).reduce((a, b) => a + b, 0)} sub={Object.entries(stats.webhook_deliveries).map(([k, v]) => `${k} ${v}`).join(" · ")} />
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="card"><p className="label">Users by plan</p>{Object.entries(stats.users_by_plan).map(([k, v]) => <p key={k} className="flex justify-between text-sm"><span>{k}</span><b>{v}</b></p>)}</div>
            <div className="card"><p className="label">Users by status</p>{Object.entries(stats.users_by_status).map(([k, v]) => <p key={k} className="flex justify-between text-sm"><span>{k}</span><b>{v}</b></p>)}</div>
            <div className="card"><p className="label">Trainings</p>{Object.entries(stats.trainings_by_status).map(([k, v]) => <p key={k} className="flex justify-between text-sm"><span>{k}</span><b>{v}</b></p>)}{!Object.keys(stats.trainings_by_status).length && <p className="text-sm text-slate-500">none yet</p>}</div>
          </div>
        </>
      )}

      {tab === "upgrades" && (
        <section className="space-y-3">
          <select className="input max-w-48" value={reqFilter} onChange={(e) => setReqFilter(e.target.value)}>
            {["PENDING", "APPROVED", "REJECTED", "CANCELED", "ALL"].map((s) => <option key={s}>{s}</option>)}
          </select>
          {reqs.length === 0 && <p className="card text-sm text-slate-400">No {reqFilter.toLowerCase()} requests.</p>}
          {reqs.map((r) => (
            <div key={r.id} className="card flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="text-sm">
                <p className="font-semibold">{r.user_email}</p>
                <p className="text-slate-400">{r.from_plan_id} → <b className="text-emerald-300">{r.requested_plan_id}</b> · {r.months} mo · {money(r.amount_cents)} · <b className={r.payment_provider === "paypal" ? "text-[#FFC439]" : "text-[#25D366]"}>{r.payment_provider === "paypal" ? "PayPal" : "WhatsApp/manual"}</b></p>
                <p className="text-xs text-slate-500">{r.payment_provider === "paypal" ? "PayPal txn / email" : "ref"}: <span className="font-mono text-slate-300">{r.payment_reference ?? "—"}</span> · {new Date(r.created_at).toLocaleString()} {r.user_note ? `· “${r.user_note}”` : ""}</p>
                {r.admin_note && <p className="text-xs text-slate-500">admin: {r.admin_note}</p>}
              </div>
              {r.status === "PENDING" ? (
                <div className="flex flex-wrap gap-2">
                  <button className="btn-primary text-xs" onClick={() => act(() => api(`/api/admin/upgrade-requests/${r.id}/approve`, { method: "POST", json: { note: prompt("Approval note (optional)") || null } }), "Approved — plan activated")}>Approve</button>
                  <button className="btn-ghost text-xs text-red-300" onClick={() => act(() => api(`/api/admin/upgrade-requests/${r.id}/reject`, { method: "POST", json: { note: prompt("Reason") || null } }), "Rejected")}>Reject</button>
                </div>
              ) : <span className="text-xs text-slate-400">{r.status}</span>}
            </div>
          ))}
        </section>
      )}

      {tab === "users" && (
        <section className="space-y-3">
          <input className="input max-w-sm" placeholder="Search email…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="card overflow-x-auto p-0">
            <table className="w-full min-w-[900px] text-xs">
              <thead className="bg-slate-900 text-left text-slate-400"><tr><th className="p-2">User</th><th className="p-2">Plan</th><th className="p-2">Status</th><th className="p-2">Tokens</th><th className="p-2">Machines</th><th className="p-2">Period end</th><th className="p-2">Actions</th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-t border-slate-800 align-top">
                    <td className="p-2"><p className="font-semibold">{u.email}</p><p className="text-slate-500">{u.role === "admin" ? "🛡️ admin · " : ""}joined {new Date(u.created_at).toLocaleDateString()}</p></td>
                    <td className="p-2">
                      <select className="input py-1! text-xs" value={u.plan_id} onChange={(e) => patchUser(u.id, { plan_id: e.target.value, months: 1 }, `Plan → ${e.target.value}`)}>
                        {["FREE", "PRO", "BUSINESS"].map((p) => <option key={p}>{p}</option>)}
                      </select>
                      <p className="mt-1 text-slate-500">{u.subscription_status}</p>
                    </td>
                    <td className="p-2"><span className={u.status === "ACTIVE" ? "text-emerald-400" : "text-red-400"}>{u.status}</span></td>
                    <td className="p-2">{u.token_balance.toLocaleString()}<p className="text-slate-500">used {u.tokens_used_period.toLocaleString()}</p></td>
                    <td className="p-2">{u.machines_online}/{u.machines} online</td>
                    <td className="p-2">{u.current_period_end ? new Date(u.current_period_end).toLocaleDateString() : "—"}</td>
                    <td className="space-x-1 space-y-1 p-2">
                      <button className="btn-ghost px-2! py-0.5! text-[11px]" onClick={() => { const n = Number(prompt("Token adjustment (e.g. 5000 or -1000)")); if (n) patchUser(u.id, { token_adjust: n }, `Tokens ${n > 0 ? "+" : ""}${n}`); }}>± tokens</button>
                      {u.role !== "admin" && (u.status === "ACTIVE" ? (
                        <>
                          <button className="btn-ghost px-2! py-0.5! text-[11px] text-amber-300" onClick={() => patchUser(u.id, { status: "SUSPENDED" }, "Suspended")}>Suspend</button>
                          <button className="btn-ghost px-2! py-0.5! text-[11px] text-red-300" onClick={() => confirm(`Ban ${u.email}?`) && patchUser(u.id, { status: "BANNED" }, "Banned")}>Ban</button>
                        </>
                      ) : (
                        <button className="btn-ghost px-2! py-0.5! text-[11px] text-emerald-300" onClick={() => patchUser(u.id, { status: "ACTIVE" }, "Reactivated")}>Reactivate</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === "machines" && (
        <div className="card overflow-x-auto p-0">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="bg-slate-900 text-left text-slate-400"><tr><th className="p-2">Machine</th><th className="p-2">Owner</th><th className="p-2">Status</th><th className="p-2">Last heartbeat</th><th className="p-2">Created</th></tr></thead>
            <tbody>{conns.map((c) => (
              <tr key={c.id} className="border-t border-slate-800">
                <td className="p-2 font-semibold">🖥️ {c.machine_name}</td><td className="p-2">{c.user_email}</td><td className="p-2"><StatusDot online={c.online} /></td>
                <td className="p-2">{c.last_heartbeat ? new Date(c.last_heartbeat).toLocaleString() : "never"}</td><td className="p-2">{new Date(c.created_at).toLocaleDateString()}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {tab === "marketplace" && <AdminMarketplace />}

      {tab === "payments" && (
        <div className="card overflow-x-auto p-0">
          <p className="p-3 text-sm">All-time revenue: <b className="text-emerald-300">{money(stats?.revenue_total_cents ?? 0)}</b> · MRR {money(stats?.mrr_cents ?? 0)}</p>
          <table className="w-full min-w-[640px] text-xs">
            <thead className="bg-slate-900 text-left text-slate-400"><tr><th className="p-2">Date</th><th className="p-2">User</th><th className="p-2">Plan</th><th className="p-2">Amount</th><th className="p-2">Provider</th><th className="p-2">Reference</th></tr></thead>
            <tbody>{pays.map((p) => (
              <tr key={p.id} className="border-t border-slate-800"><td className="p-2">{new Date(p.created_at).toLocaleString()}</td><td className="p-2">{p.user_email}</td><td className="p-2">{p.plan_id}</td><td className="p-2">{money(p.amount_cents, p.currency)}</td><td className="p-2">{p.provider}</td><td className="p-2">{p.reference ?? "—"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {tab === "audit" && (
        <section className="space-y-3">
          <input className="input max-w-sm" placeholder="Filter event type (e.g. TASK, ADMIN, SKILL)" value={auditFilter} onChange={(e) => setAuditFilter(e.target.value.toUpperCase())} />
          <div className="card max-h-[70dvh] overflow-y-auto p-0 font-mono text-[11px]">
            {audit.map((a) => (
              <div key={a.id} className="border-t border-slate-800 p-2">
                <span className="text-slate-500">{new Date(a.created_at).toLocaleString()}</span> <b className="text-sky-300">{a.event_type}</b>
                <span className="text-slate-500"> {a.connector_id?.slice(0, 8) ?? ""}</span>
                <p className="truncate text-slate-400">{JSON.stringify(a.payload_json)}</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </Shell>
  );
}
