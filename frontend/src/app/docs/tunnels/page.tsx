"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { useApiBase } from "@/lib/useBase";

const ERRORS = [
  ["401", "Access token required / wrong", "The tunnel is protected. Send X-Tunnel-Token: tnl_… or ?tunnel_token=tnl_…"],
  ["403", "Refused by machine", "The connector runs with --no-tunnels or the port isn’t in --tunnel-ports."],
  ["404", "Tunnel not found", "Unknown slug (deleted or mistyped)."],
  ["413", "Request too large", "Request body over 8 MB."],
  ["502", "Tunnel offline / local service error", "Machine offline, connector older than v1.1.0, nothing listening on the port, or response over 8 MB."],
  ["503", "Tunnel paused", "You paused it in the dashboard."],
  ["504", "Tunnel timeout", "Your local service didn’t answer within 30 s."],
];

const SCHEMA = [
  ["id", "uuid", "Primary key"],
  ["user_id", "uuid → users", "Owner (cascade delete)"],
  ["connector_id", "uuid → connectors", "Machine that serves the tunnel (cascade delete)"],
  ["slug", "varchar(48) unique", "Public path segment: /t/{slug}/"],
  ["name", "varchar(120)", "Label shown in the dashboard"],
  ["local_port", "integer 1–65535", "Port on 127.0.0.1 of that machine"],
  ["token_hash", "varchar(64) null", "SHA-256 of the optional tnl_ access token (plain token never stored)"],
  ["is_active", "boolean", "Pause / resume"],
  ["requests_count, bytes_in, bytes_out", "int / bigint", "Usage counters"],
  ["last_status, last_used_at", "int / timestamptz", "Last response status and time"],
  ["created_at", "timestamptz", "Creation time"],
];

export default function TunnelsDocs() {
  const base = useApiBase();
  return (
    <DocsLayout title="HTTP tunnels">
      <p>
        A tunnel gives a web app running on your own computer a public HTTPS address, like ngrok — but it rides on the BridgeBot connector you already run, so it’s free and needs no
        router or firewall changes. Manage tunnels under <Link href="/tunnels/">Tunnels</Link> in the dashboard.
      </p>

      <h2>How it works</h2>
      <CodeBlock code={`Internet ──HTTPS──▶ ${base}/t/{slug}/{path}?{query}
                         │  BridgeBot cloud (FastAPI)
                         │  TUNNEL_REQUEST {request_id, method, path, headers, body_b64}
                         ▼  (your connector's existing outbound WebSocket)
                  local_connector.py  ──HTTP──▶  http://127.0.0.1:{port}/{path}?{query}
                         │  TUNNEL_RESPONSE {request_id, status, headers, body_b64}
                         ▼
Internet ◀── same status, headers and bytes ──`} />
      <ul>
        <li>Every method: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS. Path and query are forwarded exactly as sent (still URL-encoded).</li>
        <li>Bodies are base64-encoded on the wire, so binary uploads/downloads (images, zips, protobuf) are byte-exact.</li>
        <li>Request headers are forwarded (minus hop-by-hop ones) plus <code>X-Forwarded-For</code>, <code>X-Forwarded-Proto</code>, <code>X-Forwarded-Host</code> and <code>X-Forwarded-Prefix: /t/{"{slug}"}</code>. <code>Host</code> is set to <code>localhost:{"{port}"}</code>.</li>
        <li>Response headers (including multiple <code>Set-Cookie</code>) come back unchanged; redirects to <code>localhost</code> or to an absolute path are rewritten to stay inside the tunnel.</li>
        <li>Limits: 8 MB request body, 8 MB response, 30 s per request. WebSocket upgrades and streaming (SSE) are not supported — responses are buffered.</li>
        <li>Plans: Free 1 tunnel · Pro 5 · Business 20.</li>
      </ul>

      <h2>Quick start</h2>
      <ol>
        <li>Run the connector v1.1.0+ (<Link href="/docs/connect/">download it</Link>) on the machine.</li>
        <li>Start something local, e.g. <code>python -m http.server 8000</code>.</li>
        <li>Dashboard → <Link href="/tunnels/">Tunnels</Link> → pick the machine, port <code>8000</code> → Create.</li>
        <li>Open the public URL. That’s it.</li>
      </ol>
      <p>Or with the API (personal <code>bbu_</code> key or login JWT):</p>
      <CodeBlock code={`curl -X POST ${base}/api/tunnels \\
  -H "Authorization: Bearer bbu_…" -H "Content-Type: application/json" \\
  -d '{"connector_id":"<machine id>","local_port":5678,"name":"n8n","slug":"my-n8n","require_token":false}'
# → {"tunnel":{"slug":"my-n8n","public_url":"${base}/t/my-n8n/",…},"access_token":null}

GET    /api/tunnels                     list (with online status + usage)
PATCH  /api/tunnels/{id}                {"is_active":false} | {"local_port":8080} | {"require_token":true}
POST   /api/tunnels/{id}/rotate-token   new tnl_ token (shown once)
DELETE /api/tunnels/{id}`} />

      <h2>Example: public n8n webhooks from your PC</h2>
      <p>n8n’s Webhook node needs a public URL. Create a tunnel to port <code>5678</code> with slug <code>my-n8n</code>, then start n8n with that URL as its base:</p>
      <CodeBlock code={`# Linux / macOS
export WEBHOOK_URL=${base}/t/my-n8n/
n8n start

# Docker
docker run -it --rm -p 5678:5678 -e WEBHOOK_URL=${base}/t/my-n8n/ n8nio/n8n

# Windows PowerShell
$env:WEBHOOK_URL="${base}/t/my-n8n/"; n8n start`} />
      <p>Now a Webhook node with path <code>order-created</code> shows its production URL as <code>{base}/t/my-n8n/webhook/order-created</code>. Any service can call it:</p>
      <CodeBlock code={`curl -X POST ${base}/t/my-n8n/webhook/order-created \\
  -H "Content-Type: application/json" \\
  -d '{"order_id": 1042, "total": 59.90}'`} />
      <p>Tip: protect the tunnel with a token if only your own services call it — but most third-party webhook senders can’t add custom headers, so use <code>?tunnel_token=…</code> in the URL for those (it is stripped before reaching n8n).</p>

      <h2>Access tokens</h2>
      <p>Tick “Protect with an access token” (or <code>{`"require_token": true`}</code>). The <code>tnl_…</code> token is shown once; we store only its SHA-256.</p>
      <CodeBlock code={`curl -H "X-Tunnel-Token: tnl_…" ${base}/t/my-app/api/status
curl "${base}/t/my-app/api/status?tunnel_token=tnl_…"`} />
      <p>Both forms are removed before the request reaches your app.</p>

      <h2>Error pages</h2>
      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-slate-900 text-left text-xs text-slate-400 uppercase"><tr><th className="p-3">Status</th><th className="p-3">Page</th><th className="p-3">Meaning</th></tr></thead>
          <tbody>{ERRORS.map(([c, t, m]) => (
            <tr key={c} className="border-t border-slate-800"><td className="p-3 font-mono text-emerald-300">{c}</td><td className="p-3 text-slate-200">{t}</td><td className="p-3 text-slate-400">{m}</td></tr>
          ))}</tbody>
        </table>
      </div>

      <h2>Security on your machine</h2>
      <ul>
        <li>The connector only ever connects to <code>127.0.0.1</code> — a tunnel can’t reach other hosts on your network.</li>
        <li>Restrict ports: <code>python local_connector.py --tunnel-ports 5678,8000</code> (or <code>BRIDGEBOT_TUNNEL_PORTS</code>, or <code>allowed_tunnel_ports</code> in the config).</li>
        <li>Disable completely: <code>--no-tunnels</code> (or <code>BRIDGEBOT_NO_TUNNELS=1</code>).</li>
        <li>Anyone with an unprotected URL can reach that local app — treat the slug as public and use a token for admin panels.</li>
      </ul>

      <h2>Wire protocol</h2>
      <CodeBlock code={`// cloud → connector
{"type":"TUNNEL_REQUEST","request_id":"3f9c…","tunnel_id":"…","port":5678,"method":"POST",
 "path":"/webhook/order-created?x=1","headers":[["content-type","application/json"],…],
 "body_b64":"eyJvcmRlcl9pZCI6MTA0Mn0=","timeout_s":30,"max_response_bytes":8388608}

// connector → cloud
{"type":"TUNNEL_RESPONSE","request_id":"3f9c…","status":200,
 "headers":[["Content-Type","application/json"],…],"body_b64":"…"}
// or on failure
{"type":"TUNNEL_RESPONSE","request_id":"3f9c…","status":502,"error":"nothing is listening on 127.0.0.1:5678"}`} />
      <p>Connectors advertise <code>{`"features":["tunnel"]`}</code> in <code>HELLO</code>; older connectors get a 502 “Connector update required” page.</p>

      <h2>Database table <code>tunnels</code></h2>
      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-slate-900 text-left text-xs text-slate-400 uppercase"><tr><th className="p-3">Column</th><th className="p-3">Type</th><th className="p-3">Notes</th></tr></thead>
          <tbody>{SCHEMA.map(([c, t, n]) => (
            <tr key={c} className="border-t border-slate-800"><td className="p-3 font-mono text-emerald-300">{c}</td><td className="p-3 font-mono text-xs text-slate-300">{t}</td><td className="p-3 text-slate-400">{n}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <p>Plus <code>plans.max_tunnels</code> (1 / 5 / 20). Full DDL in <code>db/schema.sql</code>; the backend also creates it automatically on startup.</p>
    </DocsLayout>
  );
}
