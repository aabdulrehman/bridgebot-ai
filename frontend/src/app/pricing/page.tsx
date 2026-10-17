/** Pricing page with plan comparison + FAQ. */
import MarketingLayout from "@/components/MarketingLayout";
import PlanCards from "@/components/PlanCards";
import { WHATSAPP_DISPLAY, waLink } from "@/lib/site";

const ROWS: [string, string, string, string][] = [
  ["Cloud-to-Local Bridge (live logs, remote tasks)", "✓", "✓", "✓"],
  ["Machines (connectors)", "1", "5", "25"],
  ["Remote tasks per day", "50", "1,000", "Unlimited"],
  ["On-Demand Training — platform tokens / month", "5,000", "100,000", "500,000"],
  ["Skill preview, approval & injection", "✓", "✓", "✓"],
  ["Outbound webhooks (HMAC-signed, retries)", "1", "10", "50"],
  ["Inbound webhook (trigger tasks from Zapier/n8n)", "—", "✓", "✓"],
  ["Personal REST API keys", "1", "5", "20"],
  ["Audit log history", "✓", "✓", "✓"],
  ["Support", "Community", "Email + WhatsApp", "Priority WhatsApp"],
];

const FAQ = [
  ["How do I pay?", "Click “Pay with PayPal” (paypal.me/BunnyOnMoney — Abdul Rehman Chohan), then submit your PayPal transaction ID on the Subscription page. Prefer another method? Pay/confirm via WhatsApp. An admin verifies and activates your plan — usually within a few hours."],
  ["What are platform tokens?", "Training runs on our LLM credits. Each compiled skill costs roughly 250–3,000 tokens depending on size. Allowances reset every 30-day period."],
  ["What happens when a paid period ends?", "If it is not renewed you move back to Free automatically. Your machines and skills stay; extra machines above the Free limit just can't be added."],
  ["Is my machine safe?", "The connector only dials out, runs allowlisted commands without a shell, and never exposes a port. You control the allowlist."],
];

export default function PricingPage() {
  return (
    <MarketingLayout>
      <section className="mx-auto max-w-6xl px-4 py-14">
        <div className="text-center">
          <h1 className="text-4xl font-extrabold">Pricing</h1>
          <p className="mt-2 text-slate-400">Both services in every plan. Pay for more machines, tokens and automation.</p>
        </div>
        <div className="mt-10"><PlanCards detailed /></div>

        <h2 className="mt-16 text-2xl font-bold">Compare plans</h2>
        <div className="card mt-4 overflow-x-auto p-0">
          <table className="w-full min-w-[560px] text-sm">
            <thead className="bg-slate-900 text-left text-xs text-slate-400 uppercase">
              <tr><th className="p-3">Feature</th><th className="p-3">Free</th><th className="p-3 text-emerald-300">Pro</th><th className="p-3">Business</th></tr>
            </thead>
            <tbody>
              {ROWS.map((r) => (
                <tr key={r[0]} className="border-t border-slate-800">
                  <td className="p-3 text-slate-300">{r[0]}</td><td className="p-3">{r[1]}</td><td className="p-3">{r[2]}</td><td className="p-3">{r[3]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h2 className="mt-16 text-2xl font-bold">FAQ</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {FAQ.map(([q, a]) => (
            <div key={q} className="card"><p className="font-semibold">{q}</p><p className="mt-1 text-sm text-slate-400">{a}</p></div>
          ))}
        </div>
        <p className="mt-10 text-center text-sm text-slate-400">
          Questions? <a className="text-[#25D366] hover:underline" href={waLink("Hello BridgeBot AI, I have a pricing question.")} target="_blank" rel="noopener noreferrer">WhatsApp {WHATSAPP_DISPLAY}</a>
        </p>
      </section>
    </MarketingLayout>
  );
}
