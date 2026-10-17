"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { useApiBase } from "@/lib/useBase";

const EVENTS = [
  ["connector.online", "A machine connected.", '{"connector_id":"…","machine_name":"home-pc"}'],
  ["connector.offline", "A machine disconnected.", '{"connector_id":"…","machine_name":"home-pc"}'],
  ["task.completed", "A remote task finished.", '{"task_id":"…","exit_code":0,"duration_ms":41,"error":null,"stdout_tail":["…"]}'],
  ["training.ready", "A skill finished compiling and awaits approval.", '{"training_request_id":"…","skill_id":"…","name":"…","token_cost":979}'],
  ["skill.injected", "A skill was installed on your machine (SKILL_ACK).", '{"skill_id":"…","agent_name":"default","path":"…/skills/default/….json"}'],
];

export default function WebhooksDocs() {
  const base = useApiBase();
  return (
    <DocsLayout title="Webhooks">
      <p>Webhooks connect BridgeBot to Zapier, Make, n8n, Slack bots or your own backend. Manage them under <Link href="/webhooks/">Webhooks</Link> in the dashboard.</p>

      <h2>Outbound webhooks (BridgeBot → you)</h2>
      <p>We <code>POST</code> JSON to your URL when a subscribed event happens. Free: 1 endpoint · Pro: 10 · Business: 50.</p>
      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-slate-900 text-left text-xs text-slate-400 uppercase"><tr><th className="p-3">Event</th><th className="p-3">When</th><th className="p-3">data</th></tr></thead>
          <tbody>{EVENTS.map(([e, w, d]) => (
            <tr key={e} className="border-t border-slate-800"><td className="p-3 font-mono text-emerald-300">{e}</td><td className="p-3 text-slate-300">{w}</td><td className="p-3 font-mono text-[11px] text-slate-400">{d}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <h3>Request format</h3>
      <CodeBlock code={`POST https://your-server.example.com/bridgebot
Content-Type: application/json
X-BridgeBot-Event: task.completed
X-BridgeBot-Delivery: 7f1c…            (unique id — use it for idempotency)
X-BridgeBot-Signature: t=1760711234,v1=5d2c…a9

{"id":"7f1c…","event":"task.completed","created_at":"2026-10-17T15:00:00Z","data":{…}}`} />
      <h3>Verify the signature</h3>
      <p><code>{"v1 = HMAC_SHA256(secret, t + \".\" + raw_body)"}</code> in hex. Reject if it doesn’t match or <code>t</code> is older than 5 minutes.</p>
      <CodeBlock lang="python" code={`import hmac, hashlib, time

def verify(secret: str, raw_body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])`} />
      <CodeBlock lang="js" code={`// Node / Express
const crypto = require("crypto");
app.post("/bridgebot", express.raw({ type: "application/json" }), (req, res) => {
  const { t, v1 } = Object.fromEntries(req.get("X-BridgeBot-Signature").split(",").map(p => p.split("=")));
  const mac = crypto.createHmac("sha256", process.env.WHSEC).update(\`\${t}.\`).update(req.body).digest("hex");
  if (!crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(v1))) return res.sendStatus(401);
  const event = JSON.parse(req.body);
  res.sendStatus(200);
});`} />
      <h3>Retries &amp; delivery log</h3>
      <ul>
        <li>Any non-2xx response or timeout (10 s) is retried with exponential backoff: 30 s, 2 min, 8 min, 32 min (5 attempts total).</li>
        <li>Every attempt is visible in the delivery log with status code and response body; you can retry manually.</li>
        <li>In production, URLs must be <code>https://</code> and must not resolve to private IPs.</li>
      </ul>

      <h2>Inbound webhook (you → BridgeBot) — Pro &amp; Business</h2>
      <p>Each account has a secret URL that triggers a task on one of your machines — perfect for Zapier/Make/n8n/IFTTT or cron jobs. Find it on the <Link href="/webhooks/">Webhooks</Link> page (rotate it if it leaks).</p>
      <CodeBlock code={`curl -X POST "${base}/api/hooks/in/YOUR_SECRET_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"command": "ollama run llama3.1 \\"summarise today\\"", "machine_name": "home-pc"}'
# → 202 {"task_id":"…","connector_id":"…","machine_name":"home-pc","dispatched":true}`} />
      <ul>
        <li>Body: <code>command</code> (string or argv list) + <code>connector_id</code> or <code>machine_name</code>. If omitted and exactly one machine is online, it is used.</li>
        <li>Counts against your daily task quota; the connector allowlist still applies.</li>
        <li>Combine with a <code>task.completed</code> outbound webhook to get the result back.</li>
      </ul>
    </DocsLayout>
  );
}
