"use client";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import DocsLayout from "@/components/DocsLayout";
import { CONNECTOR_README, CONNECTOR_SOURCE, GITHUB_REPO } from "@/lib/site";
import { useApiBase } from "@/lib/useBase";

export default function ConnectDocs() {
  const base = useApiBase();
  const win = `irm ${base}/downloads/install.ps1 | iex`;
  const mac = `curl -fsSL ${base}/downloads/install.sh | bash`;
  const linux = `curl -fsSL ${base}/downloads/install.sh | bash`;
  return (
    <DocsLayout title="Connect your machine">
      <p>One command installs the open-source connector, puts it in a private venv, turns on Remote Desktop, and starts it at login. It only makes <b>outbound</b> connections — no router changes.</p>
      <div className="flex flex-wrap gap-2">
        <a className="btn-ghost no-underline!" href={CONNECTOR_SOURCE} target="_blank" rel="noopener noreferrer">View source on GitHub ↗</a>
        <a className="btn-ghost no-underline!" href="/downloads/local_connector.py" download>⬇ local_connector.py</a>
      </div>
      <p className="text-xs text-slate-400">MIT · <a href={CONNECTOR_README} target="_blank" rel="noopener noreferrer">README</a> · <a href={`${GITHUB_REPO}/blob/main/SECURITY.md`} target="_blank" rel="noopener noreferrer">security</a></p>

      <h2>0 — Get a machine key first</h2>
      <p><Link href="/dashboard/">Control Room</Link> → <b>Add machine</b> → copy the <code>bbk_…</code> key (shown once).</p>

      <h2>1 — Copy-paste the command for your OS</h2>
      <p>The installer asks for the key (or pass it as shown). Remote Desktop is enabled by default.</p>

      <h3>🪟 Windows (PowerShell)</h3>
      <CodeBlock code={win} />
      <p className="text-xs text-slate-400">Or with the key inline: <code>$env:BRIDGEBOT_API_KEY=&apos;bbk_YOUR_KEY&apos;; irm {base}/downloads/install.ps1 | iex</code></p>
      <p className="text-xs text-slate-400">Creates a Scheduled Task that runs at logon. Needs Python 3.9+ from python.org (tick “Add to PATH”).</p>

      <h3>🍎 Mac (Terminal)</h3>
      <CodeBlock code={mac} />
      <p className="text-xs text-slate-400">Or: <code>curl -fsSL {base}/downloads/install.sh | bash -s -- bbk_YOUR_KEY</code></p>
      <div className="rounded-lg border border-amber-700/40 bg-amber-900/20 p-3 text-sm text-amber-100">
        <b>macOS permissions (once):</b> System Settings → Privacy &amp; Security → enable <b>Screen Recording</b> and <b>Accessibility</b> for Terminal (or iTerm) <i>and</i> Python. Then log out/in. Without these, Remote Desktop cannot see or control the screen.
      </div>

      <h3>🐧 Linux / Kali / Ubuntu (Terminal)</h3>
      <CodeBlock code={linux} />
      <p className="text-xs text-slate-400">Or: <code>curl -fsSL {base}/downloads/install.sh | bash -s -- bbk_YOUR_KEY</code></p>
      <p className="text-xs text-slate-400">Installs a systemd <b>user</b> unit with <code>BRIDGEBOT_REMOTE_DESKTOP</code>, <code>DISPLAY</code> and <code>XAUTHORITY</code>. Best on an X11 session (Kali Xfce default). Tries to apt-install <code>xdotool</code> + <code>xclip</code>.</p>

      <h2>2 — Confirm it&apos;s online</h2>
      <p>Within ~15 seconds the machine turns <b>ONLINE</b> on the <Link href="/dashboard/">dashboard</Link>. Then open <Link href="/remote/">Remote Desktop</Link>.</p>

      <h2>Manual install (advanced)</h2>
      <CodeBlock code={`mkdir bridgebot && cd bridgebot\ncurl -O ${base}/downloads/local_connector.py\npip install -r <(curl -fsSL ${base}/downloads/requirements.txt)\npython local_connector.py --server ${base} --api-key bbk_YOUR_KEY --machine-name home-pc --remote-desktop`} />

      <h2>Security model</h2>
      <ul>
        <li>Commands run without a shell (<code>;</code>, <code>|</code>, <code>&amp;&amp;</code> can’t chain).</li>
        <li>Only executables in <code>allowed_commands</code> run. Interpreters are excluded by default.</li>
        <li>Remote Desktop is owner-only. Kill switch: <code>touch ~/.bridgebot/remote_desktop_off</code> (same path on Windows under your user folder).</li>
      </ul>

      <h2>Using injected skills</h2>
      <p>Skills land in <code>skills/&lt;agent&gt;/</code>. Point your local bot / n8n at that folder, or set <code>skill_hook_url</code> in the config so every inject is POSTed to a webhook.</p>
    </DocsLayout>
  );
}
