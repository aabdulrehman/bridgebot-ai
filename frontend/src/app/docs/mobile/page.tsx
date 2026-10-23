"use client";
import Link from "next/link";
import DocsLayout from "@/components/DocsLayout";
import { useApiBase } from "@/lib/useBase";

export default function MobileDocs() {
  const base = useApiBase();
  return (
    <DocsLayout title="Live Mobilelty (Android)">
      <p>
        <b>Live Mobilelty</b> is the reverse of Remote Desktop: control your <b>Android phone</b> from a laptop
        browser over the internet. Live screen, tap, swipe, type, Back / Home / Recents. Owner-only — another BridgeBot
        account cannot open your phone. iPhone is not supported (Apple blocks remote control apps).
      </p>

      <h2>1 — Install the app</h2>
      <ol>
        <li>On the phone, open <a href={`${base}/downloads/live-mobilelty.apk`}>{base}/downloads/live-mobilelty.apk</a>.</li>
        <li>If Android blocks it, tap <b>Settings</b> → allow install from this browser / unknown sources, then retry.</li>
        <li>Open <b>Live Mobilelty</b>.</li>
      </ol>

      <h2>2 — Pair with a 6-digit code</h2>
      <ol>
        <li>On the laptop, sign in → <Link href="/mobile/">Live Mobilelty</Link> → enter a phone name → <b>Get pair code</b>.</li>
        <li>On the phone, type that 6-digit code → <b>Pair with code</b>. The app stores a private <code>bbk_…</code> key.</li>
        <li>Codes expire in 10 minutes. Wrong guesses are rate-limited.</li>
      </ol>
      <p className="text-xs text-slate-400">Advanced: paste an existing connector key from the dashboard instead of the code.</p>

      <h2>3 — Permissions (required once)</h2>
      <ol>
        <li><b>Screen capture</b> — tap <b>Allow screen capture</b> / Start and accept the system dialog. Without this, the laptop sees a black screen.</li>
        <li><b>Accessibility service</b> — Settings → Accessibility → Live Mobilelty → On. Without this, taps and swipes do nothing.</li>
        <li><b>Battery optimisation</b> — set to Unrestricted / Don&apos;t optimise so Android does not kill the connection.</li>
        <li><b>Notifications</b> — allow them. A persistent notification stays while connected; it shows when <i>your</i> account is viewing the phone, and has a <b>Stop</b> button.</li>
      </ol>

      <h2>4 — Control from the laptop</h2>
      <ol>
        <li>Open <Link href="/mobile/">/mobile/</Link>, pick the phone (🟢 online), choose quality, press <b>Connect</b>.</li>
        <li>Click = tap · drag = swipe · use Back / Home / Recents · type in the box and Send.</li>
      </ol>

      <h2>Security</h2>
      <ul>
        <li>The phone uses the same owner-scoped connector auth as a laptop. The hub refuses RD_* from any account that does not own the phone.</li>
        <li>A visible notification is always on while the service runs, and it changes when a session is active.</li>
        <li>Stop from the notification or the app any time. Re-pair rotates the key and kicks the old session.</li>
      </ul>

      <h2>Limits</h2>
      <ul>
        <li>Android only (API 26+). iOS out of scope.</li>
        <li>~0.3–1 s delay. No phone microphone / speaker audio.</li>
        <li>Some banking / DRM apps block screen capture (black frames) — that is Android, not us.</li>
        <li>File transfer phone↔laptop is not in v1 (screen + gestures only).</li>
      </ul>

      <h2>Open source</h2>
      <p>
        App source: <a href="https://github.com/aabdulrehman/bridgebot-ai/tree/main/android/live-mobilelty" target="_blank" rel="noopener noreferrer">android/live-mobilelty</a>.
        APK is built for free by GitHub Actions and published on the <code>live-mobilelty-v1</code> release.
      </p>
    </DocsLayout>
  );
}
