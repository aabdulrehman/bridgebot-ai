/** Landing page (public marketing). */
import Link from "next/link";
import MarketingLayout from "@/components/MarketingLayout";
import PlanCards from "@/components/PlanCards";

const BRIDGE_FEATURES = [
  { icon: "🛰️", title: "Remote control room", text: "Send tasks to your home/office PC from your phone. No port forwarding, no static IP — the connector dials out over secure WebSockets." },
  { icon: "🌐", title: "Free HTTP tunnels", text: "ngrok-style public URLs (/t/your-slug/) to any local port — n8n webhooks, Ollama, dev servers — with optional access tokens." },
  { icon: "📜", title: "Live stdout/stderr", text: "Watch every log line stream in real time, with exit codes, durations and a searchable audit history." },
  { icon: "🔐", title: "Safe by design", text: "Hashed API keys, command allowlist, no shell execution, timeouts and output caps on the local daemon." },
  { icon: "♻️", title: "Always reconnecting", text: "Heartbeats + exponential backoff keep the tunnel alive across Wi-Fi drops, sleeps and server restarts." },
];
const TRAIN_FEATURES = [
  { icon: "🧠", title: "Master Bot compiles skills", text: "Describe your niche; Genesis generates the system prompt, tool schemas, memory config, guardrails and evals." },
  { icon: "🪙", title: "Platform tokens, not yours", text: "Generation runs on our LLM credits. You never paste your own OpenAI/Anthropic key." },
  { icon: "👀", title: "Preview & approve", text: "Inspect every prompt and tool before it ships. Nothing reaches your machine without your approval." },
  { icon: "⚡", title: "Instant injection", text: "Approved payloads are pushed over the bridge into your n8n, Hermes, LangChain or Ollama agent." },
];
const BOT_FEATURES = [
  { icon: "🔌", title: "Universal Bot API", text: "Hermes, OpenClaw, n8n, LangChain, any HTTP/CLI bot or an external cloud bot like Grok Bot: get a bot token, long-poll / WebSocket / webhook, reply over REST." },
  { icon: "🏪", title: "Master Bot Marketplace", text: "Pick a Master Bot published by our team — skill package, persona and tools — and add your bot to its messenger group." },
  { icon: "⚡", title: "Make my bot a master", text: "One click clones the master’s skills and persona into your bot: SKILL_INJECT for connector bots, Bot API / webhook for external bots." },
  { icon: "🎓", title: "Zero-touch onboarding", text: "The master welcomes every new bot, injects its skills, runs a follow-up test and certifies it — no manual steps." },
];
const STEPS = [
  { n: 1, title: "Create a free account", text: "Free plan includes 1 machine, 50 tasks/day and 5,000 training tokens per month." },
  { n: 2, title: "Run the connector", text: "Download local_connector.py, paste your machine key and run it next to your agents." },
  { n: 3, title: "Link & train", text: "Link any bot, pick a Master Bot and click “Make my bot a master” — onboarding is automatic." },
  { n: 4, title: "Automate", text: "Signed outbound webhooks, inbound task triggers and a full REST API with personal keys." },
];

function Grid({ items }: { items: { icon: string; title: string; text: string }[] }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {items.map((f) => (
        <div key={f.title} className="card">
          <p className="text-2xl">{f.icon}</p>
          <p className="mt-2 font-semibold">{f.title}</p>
          <p className="mt-1 text-sm text-slate-400">{f.text}</p>
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  return (
    <MarketingLayout>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(16,185,129,0.18),transparent_60%)]" />
        <div className="relative mx-auto max-w-6xl px-4 pt-16 pb-12 text-center md:pt-24">
          <span className="rounded-full border border-emerald-700/50 bg-emerald-900/20 px-3 py-1 text-xs text-emerald-300">Cloud-to-Local Bridge · Universal Bot API · Master Bot Marketplace</span>
          <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-extrabold tracking-tight md:text-6xl">
            Your AI agents run at home.<br /><span className="text-emerald-400">Control them from anywhere.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-slate-400 md:text-lg">
            BridgeBot AI connects the heavy agents on your own PC — n8n, Hermes, LangChain, local LLMs — to a secure cloud control room,
            and trains them with ready-made skills compiled by our Master Bots.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/login/?mode=register" className="btn-primary px-6! py-3! text-base">Start free →</Link>
            <Link href="/docs/connect/" className="btn-ghost px-6! py-3! text-base">Connect a machine</Link>
          </div>
          <div className="mx-auto mt-12 max-w-3xl rounded-2xl border border-slate-800 bg-black/60 p-4 text-left font-mono text-[12px] shadow-2xl">
            <p className="text-slate-500">$ python local_connector.py --server https://bridgebot-ai.onrender.com --api-key bbk_••••</p>
            <p className="text-emerald-400">✅ authenticated as connector home-pc</p>
            <p className="text-sky-400">$ ollama list</p>
            <p className="text-slate-300">llama3.1:8b    4.9 GB    2 days ago</p>
            <p className="text-sky-400">⏹ exit 0 · 41 ms</p>
            <p className="text-slate-300">🧠 skill skill_riyadh_homes_assistant installed → skills/default/</p>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="mx-auto max-w-6xl space-y-6 px-4 py-12">
        <div className="text-center">
          <p className="label">Service 1</p>
          <h2 className="text-3xl font-bold">Cloud-to-Local Bridge</h2>
          <p className="mt-2 text-slate-400">A messenger hub between your phone and your machine.</p>
        </div>
        <Grid items={BRIDGE_FEATURES} />
      </section>
      <section id="training" className="mx-auto max-w-6xl space-y-6 px-4 py-12">
        <div className="text-center">
          <p className="label">Service 2</p>
          <h2 className="text-3xl font-bold">On-Demand Training & Skill Sync</h2>
          <p className="mt-2 text-slate-400">Business-ready skills for your agents, compiled in seconds.</p>
        </div>
        <Grid items={TRAIN_FEATURES} />
      </section>

      <section id="bots" className="mx-auto max-w-6xl space-y-6 px-4 py-12">
        <div className="text-center">
          <p className="label">Universal bridge</p>
          <h2 className="text-3xl font-bold">Link any bot. Train it from a Master.</h2>
          <p className="mt-2 text-slate-400">Every agent on one platform — and an automated academy that upgrades them.</p>
        </div>
        <Grid items={BOT_FEATURES} />
        <div className="flex flex-wrap justify-center gap-3">
          <Link href="/marketplace/" className="btn-primary">Browse Master Bots →</Link>
          <Link href="/docs/bots/" className="btn-ghost">Bot API & adapters</Link>
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-6xl px-4 py-12">
        <h2 className="text-center text-3xl font-bold">How it works</h2>
        <ol className="mt-8 grid gap-4 md:grid-cols-4">
          {STEPS.map((s) => (
            <li key={s.n} className="card">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500 font-bold text-slate-950">{s.n}</span>
              <p className="mt-3 font-semibold">{s.title}</p>
              <p className="mt-1 text-sm text-slate-400">{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* Pricing teaser */}
      <section className="mx-auto max-w-6xl px-4 py-12">
        <h2 className="text-center text-3xl font-bold">Simple plans</h2>
        <p className="mt-2 text-center text-slate-400">Start free. Upgrade when you add more machines.</p>
        <div className="mt-8"><PlanCards /></div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-4xl px-4 py-12">
        <div className="card flex flex-col items-center gap-4 border-emerald-700/50 bg-gradient-to-br from-emerald-900/30 to-slate-900 p-8 text-center">
          <h2 className="text-2xl font-bold">Bridge your first machine in 3 minutes</h2>
          <p className="text-slate-400">Free forever plan · no credit card · works on Windows, macOS and Linux.</p>
          <Link href="/login/?mode=register" className="btn-primary px-6! py-3!">Create free account</Link>
        </div>
      </section>
    </MarketingLayout>
  );
}
