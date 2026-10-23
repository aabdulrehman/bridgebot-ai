// Copies the local connector files into public/downloads so the site serves them
// at /downloads/local_connector.py (works on Vercel, Render and local builds).
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = [join(here, "..", "..", "connector"), "/connector"].find((d) => existsSync(join(d, "local_connector.py")));
const out = join(here, "..", "public", "downloads");
if (!src) {
  console.warn("[copy-connector] connector/ not found — downloads skipped");
  process.exit(0);
}
mkdirSync(out, { recursive: true });
for (const f of ["local_connector.py", "connector_config.example.json", "requirements.txt", "bridgebot_sdk.py", "install.sh", "install.ps1"]) copyFileSync(join(src, f), join(out, f));
console.log(`[copy-connector] copied connector files from ${src}`);
const apkCandidates = [
  join(here, "..", "..", "android", "live-mobilelty", "dist", "live-mobilelty.apk"),
  "/android/live-mobilelty/dist/live-mobilelty.apk",
  join(here, "..", "public", "downloads", "live-mobilelty.apk"),
];
const apk = apkCandidates.find((f) => existsSync(f));
if (apk) {
  copyFileSync(apk, join(out, "live-mobilelty.apk"));
  console.log(`[copy-connector] copied Live Mobilelty APK from ${apk}`);
} else {
  console.warn("[copy-connector] live-mobilelty.apk not found — phone install link will redirect to GitHub release");
}

