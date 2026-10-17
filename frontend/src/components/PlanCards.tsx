"use client";
/** Plan cards used on landing + pricing. Loads live plans from /api/plans (falls back to static copy). */
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, getToken } from "@/lib/api";
import { FALLBACK_PLANS, PAYPAL_NAME, limit, money, paypalLink, readMe, upgradeMessage, waLink } from "@/lib/site";
import type { Plan } from "@/lib/types";
import { PayPalIcon } from "./PayPalIcon";
import { WhatsAppIcon } from "./WhatsAppFab";

export default function PlanCards({ detailed = false }: { detailed?: boolean }) {
  const [plans, setPlans] = useState<Plan[]>(FALLBACK_PLANS);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    api<Plan[]>("/api/plans").then((p) => p.length && setPlans(p)).catch(() => undefined);
    setAuthed(!!getToken());
    const me = readMe();
    setEmail(me?.email ?? null);
    setCurrent(me?.plan_id ?? null);
  }, []);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {plans.map((p) => {
        const featured = p.id === "PRO";
        const isCurrent = current === p.id;
        const href = authed ? `/billing/?plan=${p.id}` : `/login/?mode=register&plan=${p.id}`;
        return (
          <div key={p.id} className={`card flex flex-col ${featured ? "border-emerald-500 shadow-emerald-900/30" : ""}`}>
            <div className="flex items-center justify-between">
              <p className="text-lg font-bold">{p.name}</p>
              {featured && <span className="rounded-full bg-emerald-500 px-2 py-0.5 text-[11px] font-bold text-slate-950">POPULAR</span>}
              {isCurrent && <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px]">CURRENT</span>}
            </div>
            <p className="mt-3 text-4xl font-extrabold">{p.price_monthly_cents ? money(p.price_monthly_cents, p.currency) : "$0"}<span className="text-sm font-normal text-slate-400">/month</span></p>
            <ul className="mt-4 flex-1 space-y-2 text-sm text-slate-300">
              {(p.features_json || []).map((f) => <li key={f} className="flex gap-2"><span className="text-emerald-400">✓</span>{f}</li>)}
            </ul>
            {detailed && (
              <dl className="mt-4 grid grid-cols-2 gap-1 rounded-xl bg-slate-950 p-3 text-xs text-slate-400">
                <dt>Machines</dt><dd className="text-right text-slate-200">{limit(p.max_machines)}</dd>
                <dt>Training tokens/mo</dt><dd className="text-right text-slate-200">{limit(p.monthly_tokens)}</dd>
                <dt>Tasks/day</dt><dd className="text-right text-slate-200">{limit(p.max_tasks_per_day)}</dd>
                <dt>Webhooks</dt><dd className="text-right text-slate-200">{limit(p.max_webhooks)}</dd>
                <dt>API keys</dt><dd className="text-right text-slate-200">{limit(p.max_api_keys)}</dd>
                <dt>Inbound webhook</dt><dd className="text-right text-slate-200">{p.inbound_webhooks ? "✓" : "—"}</dd>
              </dl>
            )}
            {p.price_monthly_cents > 0 && <p className="mt-4 text-[11px] text-slate-500">After paying, submit your PayPal transaction ID on the Subscription page — an admin activates your plan.</p>}
            <div className="mt-3 space-y-2">
              <Link href={href} className={`${featured ? "btn-primary" : "btn-ghost"} w-full`}>
                {isCurrent ? "Manage plan" : p.price_monthly_cents ? `Upgrade to ${p.name}` : "Start free"}
              </Link>
              {p.price_monthly_cents > 0 && (
                <a href={paypalLink(p.price_monthly_cents, p.currency)} target="_blank" rel="noopener noreferrer" title={`PayPal.Me — ${PAYPAL_NAME}`}
                  className="btn w-full bg-[#FFC439] text-[#003087] hover:brightness-105">
                  <PayPalIcon /> Pay with PayPal · {money(p.price_monthly_cents, p.currency)}
                </a>
              )}
              {p.price_monthly_cents > 0 && (
                <a href={waLink(upgradeMessage(p.name, email))} target="_blank" rel="noopener noreferrer"
                  className="btn w-full border border-[#25D366]/50 text-[#25D366] hover:bg-[#25D366]/10">
                  <WhatsAppIcon /> Contact on WhatsApp
                </a>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
