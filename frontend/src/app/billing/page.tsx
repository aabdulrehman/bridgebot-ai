"use client";
/** Subscription & usage: current plan, limits, request upgrade (manual → WhatsApp → admin approval). */
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import UsageBar from "@/components/Usage";
import { WhatsAppIcon } from "@/components/WhatsAppFab";
import { api } from "@/lib/api";
import { FALLBACK_PLANS, PAYPAL_ME, PAYPAL_NAME, limit, money, paypalLink, upgradeMessage, waLink } from "@/lib/site";
import { PayPalIcon } from "@/components/PayPalIcon";
import type { Plan, Subscription, UpgradeRequest } from "@/lib/types";

interface UpgradeResponse {
  request: UpgradeRequest;
  checkout: { mode: string; instructions?: string; checkout_url?: string };
  whatsapp_url: string;
  message: string;
}

export default function BillingPage() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [plans, setPlans] = useState<Plan[]>(FALLBACK_PLANS);
  const [history, setHistory] = useState<UpgradeRequest[]>([]);
  const [chosen, setChosen] = useState<string>("PRO");
  const [months, setMonths] = useState(1);
  const [method, setMethod] = useState<"paypal" | "manual">("paypal");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<UpgradeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  const load = useCallback(async () => {
    try {
      const [s, p, h] = await Promise.all([api<Subscription>("/api/billing/me"), api<Plan[]>("/api/plans"), api<UpgradeRequest[]>("/api/billing/requests")]);
      setSub(s);
      setPlans(p);
      setHistory(h);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const q = new URLSearchParams(window.location.search).get("plan");
    if (q) setChosen(q);
  }, [load]);

  const plan = plans.find((p) => p.id === chosen);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<UpgradeResponse>("/api/billing/upgrade-request", { method: "POST", json: { plan_id: chosen, months, payment_method: method, payment_reference: reference || null, note: note || null } });
      setResult(r);
      setReference("");
      await load();
      setReload((k) => k + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (id: string) => {
    try {
      await api(`/api/billing/requests/${id}/cancel`, { method: "POST" });
      setResult(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const u = sub?.usage;
  const pending = sub?.pending_request;

  return (
    <Shell balanceKey={reload}>
      <h1 className="text-xl font-bold">💳 Subscription & usage</h1>
      {error && <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300">{error}</p>}

      {sub && u && (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="card space-y-4">
            <div className="flex items-start justify-between">
              <div>
                <p className="label">Current plan</p>
                <p className="text-2xl font-extrabold text-emerald-300">{sub.plan.name}</p>
                <p className="text-xs text-slate-400">{sub.plan.price_monthly_cents ? `${money(sub.plan.price_monthly_cents)}/month` : "Free forever"} · status {sub.user.subscription_status}</p>
              </div>
              <div className="text-right text-xs text-slate-400">
                <p>Period ends</p>
                <p className="text-slate-200">{sub.user.current_period_end ? new Date(sub.user.current_period_end).toLocaleDateString() : "—"}</p>
              </div>
            </div>
            <UsageBar label="Machines" used={u.machines} max={sub.plan.max_machines} />
            <UsageBar label="Training tokens used this period" used={u.tokens_used_period} max={sub.plan.monthly_tokens} />
            <UsageBar label="Remote tasks today" used={u.tasks_today} max={sub.plan.max_tasks_per_day} />
            <UsageBar label="Webhooks" used={u.webhooks} max={sub.plan.max_webhooks} />
            <UsageBar label="API keys" used={u.api_keys} max={sub.plan.max_api_keys} />
            <p className="text-xs text-slate-400">Token balance: <b className="text-emerald-300">{u.token_balance.toLocaleString()}</b> · Inbound webhook: {sub.plan.inbound_webhooks ? "✓ included" : "Pro+"}</p>
          </section>

          <section className="card space-y-4">
            <p className="font-semibold">Change plan</p>
            {pending ? (
              <div className="space-y-3 rounded-xl border border-amber-600/50 bg-amber-950/30 p-3 text-sm">
                <p className="font-semibold text-amber-300">⏳ Upgrade to {pending.requested_plan_id} pending admin approval</p>
                <p className="text-slate-300">
                  {pending.payment_provider === "paypal"
                    ? `PayPal payment submitted (ref: ${pending.payment_reference}). An admin will verify it and activate your plan.`
                    : "Pay/confirm via WhatsApp — an admin will approve your upgrade."} Amount: {money(pending.amount_cents)} for {pending.months} month(s).
                </p>
                {pending.payment_provider !== "paypal" && (
                  <a className="btn w-full bg-[#FFC439] text-[#003087] hover:brightness-105" target="_blank" rel="noopener noreferrer" href={paypalLink(pending.amount_cents)}>
                    <PayPalIcon /> Pay with PayPal · {money(pending.amount_cents)}
                  </a>
                )}
                <a className="btn w-full bg-[#25D366] text-white hover:brightness-110" target="_blank" rel="noopener noreferrer"
                  href={waLink(upgradeMessage(plans.find((p) => p.id === pending.requested_plan_id)?.name ?? pending.requested_plan_id, sub.user.email, pending.id))}>
                  <WhatsAppIcon /> Confirm on WhatsApp
                </a>
                <button className="btn-ghost w-full text-xs" onClick={() => cancel(pending.id)}>Cancel request</button>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-3 gap-2">
                  {plans.map((p) => (
                    <button key={p.id} onClick={() => setChosen(p.id)}
                      className={`rounded-xl border p-3 text-left text-xs ${chosen === p.id ? "border-emerald-500 bg-emerald-950/30" : "border-slate-700"} ${p.id === sub.plan.id ? "opacity-60" : ""}`}>
                      <p className="font-bold text-slate-100">{p.name}</p>
                      <p>{p.price_monthly_cents ? `${money(p.price_monthly_cents)}/mo` : "$0"}</p>
                      <p className="text-slate-500">{limit(p.max_machines)} machines</p>
                    </button>
                  ))}
                </div>
                {plan && plan.id !== sub.plan.id && plan.price_monthly_cents > 0 && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="label" htmlFor="months">Months</label>
                        <select id="months" className="input" value={months} onChange={(e) => setMonths(Number(e.target.value))}>
                          {[1, 3, 6, 12].map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                      </div>
                      <div>
                        <p className="label">Total</p>
                        <p className="py-2 text-lg font-bold">{money(plan.price_monthly_cents * months, plan.currency)}</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {([["paypal", "PayPal"], ["manual", "WhatsApp / bank"]] as const).map(([m, l]) => (
                        <button key={m} type="button" onClick={() => setMethod(m)}
                          className={`rounded-xl border p-2 font-semibold ${method === m ? "border-emerald-500 bg-emerald-950/30 text-emerald-300" : "border-slate-700 text-slate-400"}`}>{l}</button>
                      ))}
                    </div>
                    {method === "paypal" ? (
                      <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950 p-3">
                        <p className="text-xs text-slate-400"><b className="text-slate-200">Step 1.</b> Pay {money(plan.price_monthly_cents * months, plan.currency)} to {PAYPAL_NAME} on PayPal ({PAYPAL_ME.replace("https://", "")}).</p>
                        <a className="btn w-full bg-[#FFC439] text-[#003087] hover:brightness-105" target="_blank" rel="noopener noreferrer" href={paypalLink(plan.price_monthly_cents * months, plan.currency)}>
                          <PayPalIcon /> Pay with PayPal · {money(plan.price_monthly_cents * months, plan.currency)}
                        </a>
                        <p className="text-xs text-slate-400"><b className="text-slate-200">Step 2.</b> Paste the PayPal transaction ID (or the PayPal email you paid from) and submit. An admin verifies and activates {plan.name}.</p>
                        <input id="ref" className="input" placeholder="PayPal transaction ID or PayPal email" value={reference} onChange={(e) => setReference(e.target.value)} />
                      </div>
                    ) : (
                      <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950 p-3">
                        <p className="text-xs text-slate-400">Pay/confirm via WhatsApp, admin will approve. Submit the request, then message us with your email and payment proof.</p>
                        <input id="ref" className="input" placeholder="Payment reference (optional)" value={reference} onChange={(e) => setReference(e.target.value)} />
                      </div>
                    )}
                    <div>
                      <label className="label" htmlFor="note">Note (optional)</label>
                      <input id="note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
                    </div>
                    <button className="btn-primary w-full" disabled={busy || (method === "paypal" && !reference.trim())} onClick={submit}>
                      {busy ? "Submitting…" : method === "paypal" ? `Submit PayPal payment for ${plan.name}` : `Request ${plan.name} upgrade`}
                    </button>
                    <a className="btn w-full border border-[#25D366]/50 text-[#25D366] hover:bg-[#25D366]/10" target="_blank" rel="noopener noreferrer"
                      href={waLink(upgradeMessage(plan.name, sub.user.email))}><WhatsAppIcon /> Questions? Contact on WhatsApp</a>
                  </div>
                )}
                {plan && plan.id !== sub.plan.id && plan.price_monthly_cents === 0 && (
                  <button className="btn-ghost w-full" disabled={busy} onClick={submit}>Downgrade to Free</button>
                )}
                {plan && plan.id === sub.plan.id && <p className="text-xs text-slate-500">This is your current plan.</p>}
              </>
            )}
            {result && <p className="rounded-lg bg-emerald-950/50 p-2 text-sm text-emerald-300">{result.message}</p>}
          </section>
        </div>
      )}

      {history.length > 0 && (
        <section className="card overflow-x-auto p-0">
          <p className="p-3 font-semibold">Upgrade history</p>
          <table className="w-full min-w-[520px] text-xs">
            <thead className="bg-slate-900 text-left text-slate-400"><tr><th className="p-2">Date</th><th className="p-2">From → To</th><th className="p-2">Amount</th><th className="p-2">Method / ref</th><th className="p-2">Status</th><th className="p-2">Admin note</th></tr></thead>
            <tbody>{history.map((h) => (
              <tr key={h.id} className="border-t border-slate-800">
                <td className="p-2">{new Date(h.created_at).toLocaleString()}</td><td className="p-2">{h.from_plan_id} → {h.requested_plan_id}</td>
                <td className="p-2">{money(h.amount_cents)}</td><td className="p-2">{h.payment_provider}{h.payment_reference ? ` · ${h.payment_reference}` : ""}</td><td className="p-2">{h.status}</td><td className="p-2 text-slate-400">{h.admin_note ?? ""}</td>
              </tr>
            ))}</tbody>
          </table>
        </section>
      )}
    </Shell>
  );
}
