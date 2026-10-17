"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { useApiBase } from "@/lib/useBase";

export default function GettingStarted() {
  const base = useApiBase();
  return (
    <DocsLayout title="Getting started">
      <p>BridgeBot AI has two services that share one account:</p>
      <ul>
        <li><b>Cloud-to-Local Bridge</b> — a small Python daemon on your PC keeps a secure WebSocket open to <code>{base}</code>. From the dashboard you dispatch commands and watch their stdout/stderr live.</li>
        <li><b>On-Demand Training</b> — describe your business niche; the Master Bot compiles a JSON skill (system prompt, tools, memory, guardrails). After you approve, it is pushed into your local agent.</li>
      </ul>
      <h2>1. Create your account</h2>
      <p><Link href="/login/?mode=register">Register</Link> — every account starts on the <b>Free</b> plan (1 machine, 50 tasks/day, 5,000 training tokens/month). See <Link href="/pricing/">Pricing</Link>.</p>
      <h2>2. Add a machine</h2>
      <p>In the <Link href="/dashboard/">Control Room</Link> type a machine name and click <b>Add</b>. Copy the <code>bbk_…</code> key — it is shown only once.</p>
      <h2>3. Run the connector</h2>
      <p>Follow <Link href="/docs/connect/">Connect your machine</Link>. The short version:</p>
      <CodeBlock code={`pip install websockets\ncurl -O ${base}/downloads/local_connector.py\npython local_connector.py --server ${base} --api-key bbk_YOUR_KEY --machine-name home-pc`} />
      <h2>4. Send your first task</h2>
      <p>Pick the machine, type <code>echo hello</code> and press <b>Run</b>. The output appears in the live console.</p>
      <h2>5. Train your bot</h2>
      <p>Open <Link href="/train/">Train My Bot</Link>, fill in the 5 steps, review the compiled skill and click <b>Approve &amp; inject</b>. The file lands in <code>skills/&lt;agent&gt;/</code> on your PC.</p>
      <h2>6. Automate</h2>
      <ul>
        <li>Create a personal API key under <Link href="/api-keys/">API keys</Link> and use the <Link href="/docs/api/">REST API</Link>.</li>
        <li>Receive signed events with <Link href="/docs/webhooks/">webhooks</Link>, or trigger tasks with your inbound webhook URL (Pro+).</li>
      </ul>
    </DocsLayout>
  );
}
