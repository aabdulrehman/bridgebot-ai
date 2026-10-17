"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { CONNECTOR_README, CONNECTOR_SOURCE, GITHUB_REPO } from "@/lib/site";
import { useApiBase } from "@/lib/useBase";

export default function ConnectDocs() {
  const base = useApiBase();
  return (
    <DocsLayout title="Connect your machine">
      <p>The connector is a single, <b>open-source (MIT)</b> Python file. It only makes <b>outbound</b> connections, so no router/firewall changes are needed. Read every line before you run it.</p>
      <div className="flex flex-wrap gap-2">
        <a className="btn-primary no-underline!" href="/downloads/local_connector.py" download>⬇ Download local_connector.py</a>
        <a className="btn-ghost no-underline!" href={CONNECTOR_SOURCE} target="_blank" rel="noopener noreferrer" data-testid="view-source">View source on GitHub ↗</a>
        <a className="btn-ghost no-underline!" href="/downloads/connector_config.example.json" download>⬇ connector_config.example.json</a>
      </div>
      <p className="text-xs text-slate-400">MIT licensed · <a href={CONNECTOR_README} target="_blank" rel="noopener noreferrer">connector README</a> · <a href={`${GITHUB_REPO}/blob/main/SECURITY.md`} target="_blank" rel="noopener noreferrer">security policy</a> · <a href={`${GITHUB_REPO}/blob/main/docs/phase1-testing.md`} target="_blank" rel="noopener noreferrer">resilience test results</a></p>

      <h2>Step 1 — Install Python 3.9+</h2>
      <ul>
        <li><b>Windows:</b> install from python.org (tick “Add to PATH”).</li>
        <li><b>macOS:</b> <code>brew install python</code></li>
        <li><b>Linux:</b> usually pre-installed (<code>python3 --version</code>).</li>
      </ul>

      <h2>Step 2 — Download the connector</h2>
      <CodeBlock code={`mkdir bridgebot && cd bridgebot\ncurl -O ${base}/downloads/local_connector.py\npip install websockets          # (use pip3 on macOS/Linux if needed)`} />
      <p>Windows PowerShell: <code>Invoke-WebRequest {base}/downloads/local_connector.py -OutFile local_connector.py</code></p>

      <h2>Step 3 — Get a machine key</h2>
      <p>Dashboard → <Link href="/dashboard/">Control Room</Link> → <b>Add machine</b>. Copy the <code>bbk_…</code> key (shown once; you can rotate it later).</p>

      <h2>Step 4 — Run it</h2>
      <CodeBlock code={`python local_connector.py --server ${base} --api-key bbk_YOUR_KEY --machine-name home-pc`} />
      <p>You should see <code>✅ authenticated as connector …</code> and the machine turns <b>ONLINE</b> in the dashboard.</p>

      <h2>Step 5 (optional) — Config file</h2>
      <p>Save as <code>connector_config.json</code> next to the script, then just run <code>python local_connector.py</code>:</p>
      <CodeBlock lang="json" code={`{
  "server_url": "${base}",
  "api_key": "bbk_YOUR_KEY",
  "machine_name": "home-pc",
  "allowed_commands": ["echo", "ls", "dir", "whoami", "nvidia-smi", "ollama", "n8n", "git", "docker"],
  "skills_dir": "./skills",
  "skill_hook_url": null,
  "agents": [{"name": "default", "type": "generic"}, {"name": "n8n-main", "type": "n8n"}]
}`} />
      <p>Environment variables also work: <code>BRIDGEBOT_SERVER_URL</code>, <code>BRIDGEBOT_API_KEY</code>, <code>BRIDGEBOT_MACHINE_NAME</code>, <code>BRIDGEBOT_SKILLS_DIR</code>.</p>

      <h2>Security model</h2>
      <ul>
        <li>Commands run without a shell (<code>;</code>, <code>|</code>, <code>&amp;&amp;</code> can’t chain).</li>
        <li>Only executables in <code>allowed_commands</code> run. Interpreters (python/node/bash/powershell) are excluded by default — add them only if you accept arbitrary code execution.</li>
        <li>Per-task timeout, output caps and remote cancel.</li>
      </ul>

      <h2>Run it in the background</h2>
      <h3>Linux (systemd)</h3>
      <CodeBlock code={`# /etc/systemd/system/bridgebot.service
[Unit]
Description=BridgeBot connector
After=network-online.target

[Service]
WorkingDirectory=/home/YOU/bridgebot
ExecStart=/usr/bin/python3 local_connector.py
Restart=always
User=YOU

[Install]
WantedBy=multi-user.target

sudo systemctl enable --now bridgebot`} />
      <h3>Windows</h3>
      <p>Task Scheduler → Create Task → “At log on” → Action: <code>pythonw.exe C:\bridgebot\local_connector.py</code>.</p>
      <h3>macOS</h3>
      <p>Use a LaunchAgent plist or simply <code>nohup python3 local_connector.py &amp;</code>.</p>

      <h2>Using injected skills</h2>
      <p>Each approved skill is saved to <code>skills/&lt;agent&gt;/&lt;skill_id&gt;.json</code> and listed in <code>active_skills.json</code>. Load <code>system_prompt</code> as the system message, register <code>tools[]</code> (OpenAI function-calling schema), seed memory from <code>memory.seed_facts</code>. Set <code>skill_hook_url</code> (e.g. an n8n Webhook node) to receive each skill instantly.</p>

      <h2>Troubleshooting</h2>
      <ul>
        <li><b>“❌ the cloud refused this connector: Invalid API key”</b> (close code 4401, or HTTP 403 on old servers) — wrong or rotated key: copy a fresh one from the Control Room. The connector retries slowly (every 1–4 min) so it never floods the server.</li>
        <li><b>“Account suspended / banned”</b> (4403/4003) — contact support on WhatsApp.</li>
        <li><b>“another connector process is using this API key”</b> (4000) — the same key runs twice; stop one copy (each machine needs its own key).</li>
        <li><b>Reconnecting…</b> — network change, sleep/wake or the free server waking up (~1 min). The connector retries forever with backoff and re-delivers task output queued while offline.</li>
        <li><b>Output arrives in bursts</b> — the program buffers when piped. Python is unbuffered automatically; for others use <code>stdbuf -oL your-command</code> (Linux) or its line-buffered flag.</li>
        <li><b>Behind a corporate proxy</b> — set <code>HTTPS_PROXY=http://proxy:3128</code>.</li>
        <li><b>“not in allowed_commands”</b> — add the executable to your config.</li>
      </ul>
    </DocsLayout>
  );
}
