"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { useApiBase } from "@/lib/useBase";

interface Ep { m: "GET" | "POST" | "PATCH" | "DELETE" | "WS"; p: string; d: string; auth: string; body?: string }

const GROUPS: { name: string; eps: Ep[] }[] = [
  { name: "Auth", eps: [
    { m: "POST", p: "/api/auth/register", d: "Create account (Free plan). Returns JWT.", auth: "none", body: '{"email":"you@example.com","password":"min8chars"}' },
    { m: "POST", p: "/api/auth/login", d: "Email + password → JWT.", auth: "none", body: '{"email":"you@example.com","password":"..."}' },
    { m: "GET", p: "/api/auth/me", d: "Profile, role, plan, token balance.", auth: "JWT / API key" },
  ] },
  { name: "Machines (connectors)", eps: [
    { m: "GET", p: "/api/connectors", d: "List machines with live ONLINE/OFFLINE status and agents.", auth: "JWT / API key" },
    { m: "POST", p: "/api/connectors", d: "Add a machine (plan limit). Returns bbk_ key once.", auth: "JWT / API key", body: '{"machine_name":"home-pc"}' },
    { m: "POST", p: "/api/connectors/{id}/rotate-key", d: "Issue a new connector key.", auth: "JWT / API key" },
    { m: "DELETE", p: "/api/connectors/{id}", d: "Delete machine and disconnect it.", auth: "JWT / API key" },
    { m: "GET", p: "/api/connectors/{id}/audit", d: "Recent audit events for one machine.", auth: "JWT / API key" },
  ] },
  { name: "Tasks", eps: [
    { m: "POST", p: "/api/trigger-task", d: "Run an allowlisted command on a machine. Output streams on /ws/dashboard and task.completed webhook.", auth: "JWT / API key", body: '{"connector_id":"<uuid>","command":"echo hello","timeout_s":60}' },
    { m: "POST", p: "/api/hooks/in/{token}", d: "Inbound webhook (Pro+): trigger a task with just the secret URL.", auth: "secret URL", body: '{"command":"echo from zapier","machine_name":"home-pc"}' },
  ] },
  { name: "Training", eps: [
    { m: "POST", p: "/api/training/submit", d: "Queue a niche for the Master Bot (status PENDING→TRAINING→READY).", auth: "JWT / API key", body: '{"niche_requirements_text":"Dental clinic in Jeddah...","business_name":"Smile","goals":["book appointments"],"target_connector_id":"<uuid>","target_agent_name":"default"}' },
    { m: "GET", p: "/api/training", d: "Your last 50 training requests.", auth: "JWT / API key" },
    { m: "GET", p: "/api/training/{id}", d: "One request incl. generated_payload_json.", auth: "JWT / API key" },
    { m: "POST", p: "/api/training/approve", d: "Approve a READY skill and inject it (SKILL_INJECT).", auth: "JWT / API key", body: '{"training_request_id":"<uuid>"}' },
  ] },
  { name: "Webhooks", eps: [
    { m: "GET", p: "/api/webhooks", d: "List outbound webhooks (incl. signing secret).", auth: "JWT / API key" },
    { m: "POST", p: "/api/webhooks", d: "Register a URL for events.", auth: "JWT / API key", body: '{"url":"https://example.com/hook","events":["task.completed","connector.offline"]}' },
    { m: "PATCH", p: "/api/webhooks/{id}", d: "Update url/events/is_active.", auth: "JWT / API key", body: '{"is_active":false}' },
    { m: "POST", p: "/api/webhooks/{id}/test", d: "Send a webhook.test event.", auth: "JWT / API key" },
    { m: "POST", p: "/api/webhooks/{id}/rotate-secret", d: "New HMAC secret.", auth: "JWT / API key" },
    { m: "DELETE", p: "/api/webhooks/{id}", d: "Delete.", auth: "JWT / API key" },
    { m: "GET", p: "/api/webhooks/deliveries", d: "Delivery log (status, attempts, response).", auth: "JWT / API key" },
    { m: "POST", p: "/api/webhooks/deliveries/{id}/retry", d: "Retry a delivery now.", auth: "JWT / API key" },
    { m: "GET", p: "/api/webhooks/inbound", d: "Your inbound webhook path/token.", auth: "JWT / API key" },
    { m: "POST", p: "/api/webhooks/inbound/rotate", d: "Rotate inbound token.", auth: "JWT / API key" },
  ] },
  { name: "API keys", eps: [
    { m: "GET", p: "/api/api-keys", d: "List personal keys (prefix only).", auth: "JWT / API key" },
    { m: "POST", p: "/api/api-keys", d: "Create key (plan limit). Raw bbu_ key returned once.", auth: "JWT / API key", body: '{"name":"ci-server"}' },
    { m: "DELETE", p: "/api/api-keys/{id}", d: "Revoke key.", auth: "JWT / API key" },
  ] },
  { name: "Billing", eps: [
    { m: "GET", p: "/api/plans", d: "Public plan catalogue.", auth: "none" },
    { m: "GET", p: "/api/billing/me", d: "Plan, limits, usage, pending request, WhatsApp link.", auth: "JWT / API key" },
    { m: "POST", p: "/api/billing/upgrade-request", d: "Request a plan change (admin approves after WhatsApp confirmation).", auth: "JWT / API key", body: '{"plan_id":"PRO","months":1,"payment_reference":"TRX-123"}' },
    { m: "GET", p: "/api/billing/requests", d: "Your upgrade requests.", auth: "JWT / API key" },
    { m: "POST", p: "/api/billing/requests/{id}/cancel", d: "Cancel a pending request.", auth: "JWT / API key" },
  ] },
  { name: "Realtime (WebSocket)", eps: [
    { m: "WS", p: "/ws/dashboard?token=<JWT>", d: "Live events: SNAPSHOT, CONNECTOR_STATUS, TASK_DISPATCHED, LOG, TASK_RESULT, TRAINING_STATUS, SKILL_ACK…", auth: "JWT (query)" },
    { m: "WS", p: "/ws/connect", d: "Connector tunnel (used by local_connector.py).", auth: "Authorization: Bearer bbk_…" },
  ] },
];

const COLORS: Record<string, string> = { GET: "text-sky-300", POST: "text-emerald-300", PATCH: "text-amber-300", DELETE: "text-red-300", WS: "text-fuchsia-300" };

function curl(base: string, e: Ep): string {
  if (e.m === "WS") return `# websocat "${base.replace(/^http/, "ws")}${e.p}"`;
  const auth = e.auth.includes("API key") ? ` \\\n  -H "Authorization: Bearer $BRIDGEBOT_API_KEY"` : "";
  const body = e.body ? ` \\\n  -H "Content-Type: application/json" \\\n  -d '${e.body}'` : "";
  return `curl -X ${e.m} "${base}${e.p}"${auth}${body}`;
}

export default function ApiDocs() {
  const base = useApiBase();
  return (
    <DocsLayout title="API reference">
      <p>Base URL: <code>{base}</code>. JSON in/out. Interactive OpenAPI docs: <a href={`${base}/docs`} target="_blank" rel="noopener noreferrer">{base}/docs</a>.</p>
      <h2>Authentication</h2>
      <p>Create a personal key under <Link href="/api-keys/">API keys</Link> and send it as a bearer token (or <code>X-API-Key</code> header). Keys start with <code>bbu_</code>, are shown once, and can be revoked any time. Dashboard JWTs work too.</p>
      <CodeBlock code={`export BRIDGEBOT_API_KEY=bbu_xxxxxxxxxxxxxxxx\ncurl "${base}/api/connectors" -H "Authorization: Bearer $BRIDGEBOT_API_KEY"`} />
      <h3>Errors &amp; limits</h3>
      <ul>
        <li><code>401</code> missing/invalid credentials · <code>403</code> suspended account or admin-only · <code>404</code> not yours / not found</li>
        <li><code>402</code> plan limit reached (machines, keys, webhooks, feature) · <code>429</code> daily task quota · <code>409</code> machine offline</li>
      </ul>
      <h3>Quick example: run a command and read the result</h3>
      <CodeBlock code={`CID=$(curl -s "${base}/api/connectors" -H "Authorization: Bearer $BRIDGEBOT_API_KEY" | jq -r '.[0].id')\ncurl -s -X POST "${base}/api/trigger-task" -H "Authorization: Bearer $BRIDGEBOT_API_KEY" \\\n  -H "Content-Type: application/json" -d "{\\"connector_id\\":\\"$CID\\",\\"command\\":\\"nvidia-smi\\"}"\n# → {"task_id":"…","dispatched":true}  — output arrives via /ws/dashboard or a task.completed webhook`} />
      {GROUPS.map((g) => (
        <section key={g.name}>
          <h2>{g.name}</h2>
          <div className="space-y-4">
            {g.eps.map((e) => (
              <div key={e.m + e.p} className="card space-y-2">
                <p className="font-mono text-sm"><b className={COLORS[e.m]}>{e.m}</b> <span className="text-slate-200">{e.p}</span></p>
                <p className="text-xs! text-slate-400!">{e.d} · <span className="text-slate-500">auth: {e.auth}</span></p>
                <CodeBlock code={curl(base, e)} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </DocsLayout>
  );
}
