"use client";
/** Shared footer: links, subscription plans summary, current plan (if logged in), WhatsApp contact. */
import Link from "next/link";
import { useEffect, useState } from "react";
import { WhatsAppIcon } from "./WhatsAppFab";
import { CONNECTOR_SOURCE, FALLBACK_PLANS, GITHUB_REPO, WHATSAPP_DISPLAY, money, readMe, upgradeMessage, waLink, type CachedMe } from "@/lib/site";

export default function Footer() {
  const [me, setMe] = useState<CachedMe | null>(null);
  useEffect(() => {
    const sync = () => setMe(readMe());
    sync();
    window.addEventListener("bridgebot-me", sync);
    return () => window.removeEventListener("bridgebot-me", sync);
  }, []);
  return (
    <footer className="mt-16 border-t border-slate-800 bg-slate-950">
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-10 md:grid-cols-4">
        <div className="space-y-2">
          <p className="font-bold">BridgeBot <span className="text-emerald-400">AI</span></p>
          <p className="text-sm text-slate-400">Control your local AI agents from anywhere and sync trained skills over a secure bridge.</p>
          <a href={waLink("Hello BridgeBot AI, I have a question.")} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 text-sm text-[#25D366] hover:underline">
            <WhatsAppIcon /> {WHATSAPP_DISPLAY}
          </a>
        </div>
        <div>
          <p className="label">Product</p>
          <ul className="space-y-1 text-sm text-slate-400">
            <li><Link href="/#features" className="hover:text-white">Cloud-to-Local Bridge</Link></li>
            <li><Link href="/#training" className="hover:text-white">On-Demand Training</Link></li>
            <li><Link href="/marketplace/" className="hover:text-white">Master Bot Marketplace</Link></li>
            <li><Link href="/pricing/" className="hover:text-white">Pricing</Link></li>
            <li><Link href="/dashboard/" className="hover:text-white">Dashboard</Link></li>
          </ul>
        </div>
        <div>
          <p className="label">Docs</p>
          <ul className="space-y-1 text-sm text-slate-400">
            <li><Link href="/docs/" className="hover:text-white">Getting started</Link></li>
            <li><Link href="/docs/connect/" className="hover:text-white">Connect your machine</Link></li>
            <li><Link href="/docs/bots/" className="hover:text-white">Bot API &amp; adapters</Link></li>
            <li><Link href="/docs/api/" className="hover:text-white">API reference</Link></li>
            <li><Link href="/docs/tunnels/" className="hover:text-white">HTTP tunnels</Link></li>
            <li><a href={CONNECTOR_SOURCE} target="_blank" rel="noopener noreferrer" className="hover:text-white">Connector source (MIT) ↗</a></li>
            <li><Link href="/docs/webhooks/" className="hover:text-white">Webhooks</Link></li>
          </ul>
        </div>
        <div>
          <p className="label">Subscription</p>
          {me ? (
            <div className="space-y-2 text-sm">
              <p className="text-slate-300">Signed in as <span className="text-emerald-300">{me.email}</span></p>
              <p>Current plan: <b className="text-emerald-300">{me.plan_name ?? me.plan_id}</b></p>
              <Link href="/billing/" className="text-sky-300 hover:underline">Manage subscription →</Link>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Start on Free — upgrade any time.</p>
          )}
          <ul className="mt-3 space-y-1 text-xs text-slate-400">
            {FALLBACK_PLANS.map((p) => (
              <li key={p.id} className="flex justify-between gap-2">
                <span>{p.name} · {p.max_machines} machine{p.max_machines === 1 ? "" : "s"} · {(p.monthly_tokens / 1000).toLocaleString()}k tokens</span>
                <a className="shrink-0 text-emerald-400 hover:underline" target="_blank" rel="noopener noreferrer" href={waLink(upgradeMessage(p.name, me?.email))}>
                  {p.price_monthly_cents ? `${money(p.price_monthly_cents)}/mo` : "Free"}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="border-t border-slate-900 py-4 text-center text-xs text-slate-600">
        © {new Date().getFullYear()} BridgeBot AI · Payments via PayPal (paypal.me/BunnyOnMoney) or WhatsApp · verified by our team · Built on a 100% free stack · <a href={GITHUB_REPO} target="_blank" rel="noopener noreferrer" className="hover:text-white">Open source on GitHub</a>
      </div>
    </footer>
  );
}
