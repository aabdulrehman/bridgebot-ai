/** Site-wide constants, WhatsApp helpers and the static plan catalogue (fallback when the API sleeps). */
import type { Plan } from "./types";

export const SITE_NAME = "BridgeBot AI";
export const WHATSAPP_NUMBER = "966567009846";
export const WHATSAPP_DISPLAY = "+966 56 700 9846";

export const PAYPAL_ME = "https://paypal.me/BunnyOnMoney";
export const PAYPAL_NAME = "Abdul Rehman Chohan";

/** PayPal.Me link for an amount, e.g. https://paypal.me/BunnyOnMoney/19USD */
export function paypalLink(cents: number, currency = "USD"): string {
  const amount = cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
  return `${PAYPAL_ME}/${amount}${currency}`;
}

/** wa.me deep link with a prefilled message. */
export function waLink(text: string): string {
  return `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(text)}`;
}

export function upgradeMessage(planName: string, email?: string | null, requestId?: string | null): string {
  let m = `Hello BridgeBot AI, I want to subscribe to the ${planName} plan.`;
  if (email) m += ` Account email: ${email}.`;
  if (requestId) m += ` Upgrade request: ${requestId.slice(0, 8)}.`;
  return m;
}

export function money(cents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: cents % 100 ? 2 : 0 }).format(cents / 100);
}

export function limit(n: number, unit = ""): string {
  if (n === -1) return "Unlimited";
  if (n === 0) return "—";
  return `${n.toLocaleString()}${unit}`;
}

/** Mirrors backend/app/billing/plans.py — the API is the source of truth. */
export const FALLBACK_PLANS: Plan[] = [
  { id: "FREE", name: "Free", price_monthly_cents: 0, currency: "USD", monthly_tokens: 5000, max_machines: 1, max_tasks_per_day: 50, max_webhooks: 1, max_api_keys: 1, max_bots: 1, training_access: true, inbound_webhooks: false, sort_order: 0,
    features_json: ["Cloud-to-Local Bridge: 1 machine", "Live stdout/stderr log streaming", "50 remote tasks / day", "On-Demand Training: 5,000 platform tokens / month (~5 skills)", "1 outbound webhook", "1 personal API key", "Community support"] },
  { id: "PRO", name: "Pro", price_monthly_cents: 1900, currency: "USD", monthly_tokens: 100000, max_machines: 5, max_tasks_per_day: 1000, max_webhooks: 10, max_api_keys: 5, max_bots: 10, training_access: true, inbound_webhooks: true, sort_order: 1,
    features_json: ["Cloud-to-Local Bridge: 5 machines", "Live log streaming + audit history", "1,000 remote tasks / day", "On-Demand Training: 100,000 platform tokens / month", "10 outbound webhooks (HMAC-signed, retries)", "Inbound webhook → trigger tasks from Zapier/n8n/Make", "5 personal API keys", "Email support"] },
  { id: "BUSINESS", name: "Business", price_monthly_cents: 7900, currency: "USD", monthly_tokens: 500000, max_machines: 25, max_tasks_per_day: -1, max_webhooks: 50, max_api_keys: 20, max_bots: 50, training_access: true, inbound_webhooks: true, sort_order: 2,
    features_json: ["Cloud-to-Local Bridge: 25 machines", "Unlimited remote tasks", "On-Demand Training: 500,000 platform tokens / month", "50 outbound webhooks + inbound webhook", "20 personal API keys", "Priority support & onboarding"] },
];

const ME_KEY = "bridgebot_me";
export interface CachedMe { email: string; plan_id: string; plan_name?: string; role: string }
export function cacheMe(me: CachedMe | null): void {
  if (typeof window === "undefined") return;
  if (me) localStorage.setItem(ME_KEY, JSON.stringify(me));
  else localStorage.removeItem(ME_KEY);
  window.dispatchEvent(new Event("bridgebot-me"));
}
export function readMe(): CachedMe | null {
  if (typeof window === "undefined") return null;
  try {
    return JSON.parse(localStorage.getItem(ME_KEY) || "null");
  } catch {
    return null;
  }
}
