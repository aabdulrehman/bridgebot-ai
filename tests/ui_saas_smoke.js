// Headless UI smoke for the SaaS flow: pricing PayPal buttons -> register -> PayPal upgrade request -> admin approves.
const puppeteer = require(process.env.PUPPETEER || "puppeteer-core");
const BASE = process.env.BASE || "http://localhost:8099";
const ADMIN = process.env.ADMIN || "abaudii12@gmail.com";
const ADMIN_PW = process.env.ADMIN_PW || "AdminPass123!";
const SHOTS = process.env.SHOTS || "/workspace/shots";
const ok = (c, m) => { if (!c) { console.error("✘ " + m); process.exit(1); } console.log("✔ " + m); };
(async () => {
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"] });
  const p = await b.newPage(); await p.setViewport({ width: 1280, height: 900 });
  const errs = []; p.on("pageerror", (e) => errs.push(p.url() + " " + e.message.slice(0, 60)));
  for (const path of ["/", "/pricing/", "/docs/", "/docs/connect/", "/docs/api/", "/docs/webhooks/"]) {
    const r = await p.goto(BASE + path, { waitUntil: "networkidle0" }); ok(r.status() === 200, `GET ${path} 200`);
  }
  await p.goto(BASE + "/pricing/", { waitUntil: "networkidle0" });
  const pp = await p.$$eval("a[href*='paypal.me']", (a) => a.map((x) => x.href));
  ok(pp.includes("https://paypal.me/BunnyOnMoney/19USD") && pp.includes("https://paypal.me/BunnyOnMoney/79USD"), "pricing PayPal buttons " + pp.join(" "));
  const wa = await p.$$eval("a[href*='wa.me/966567009846']", (a) => a.length); ok(wa >= 3, `WhatsApp links on pricing: ${wa}`);
  await p.screenshot({ path: SHOTS + "/pricing.png", fullPage: true });
  // register user
  const email = `ui${Date.now()}@example.com`;
  await p.goto(BASE + "/login/?mode=register&plan=PRO", { waitUntil: "networkidle0" });
  await p.type("input[type=email]", email); await p.type("input[type=password]", "Passw0rd!123");
  await p.click("form button.btn-primary"); await p.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 15000 });
  await p.goto(BASE + "/billing/", { waitUntil: "networkidle0" });
  await p.waitForSelector("#ref", { timeout: 10000 });
  const bp = await p.$$eval("a[href*='paypal.me']", (a) => a.map((x) => x.href)); ok(bp.some((h) => /BunnyOnMoney\/(19|79)USD/.test(h)), "billing PayPal button " + bp[0]);
  await p.type("#ref", "PAYID-UI-TEST-123");
  const [btn] = await p.$$("xpath/.//button[contains(., 'Submit PayPal payment')]"); ok(!!btn, "submit PayPal button present");
  await btn.click(); await p.waitForFunction(() => document.body.innerText.includes("pending admin approval"), { timeout: 10000 });
  ok(true, "upgrade request pending shown"); await p.screenshot({ path: SHOTS + "/billing.png", fullPage: true });
  // admin
  await p.evaluate(() => localStorage.clear());
  await p.goto(BASE + "/login/", { waitUntil: "networkidle0" });
  await p.type("input[type=email]", ADMIN); await p.type("input[type=password]", ADMIN_PW);
  await p.click("form button.btn-primary"); await p.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 15000 });
  await p.goto(BASE + "/admin/", { waitUntil: "networkidle0" });
  await p.waitForFunction(() => document.body.innerText.includes("Users"), { timeout: 10000 });
  await p.screenshot({ path: SHOTS + "/admin.png", fullPage: true });
  const tabs = await p.$$("xpath/.//button[contains(., 'Upgrade')]"); if (tabs[0]) await tabs[0].click();
  await p.waitForFunction(() => document.body.innerText.includes("PAYID-UI-TEST-123"), { timeout: 10000 }); ok(true, "admin sees PayPal txn id");
  await p.screenshot({ path: SHOTS + "/admin_upgrades.png", fullPage: true });
  const [ap] = await p.$$("xpath/.//button[normalize-space(.)='Approve' or starts-with(normalize-space(.),'Approve')]"); ok(!!ap, "approve button");
  p.on("dialog", (d) => d.accept()); await ap.click(); await new Promise((r) => setTimeout(r, 1500));
  const st = await p.evaluate(async (em) => { const t = localStorage.getItem("bridgebot_token"); const r = await fetch("/api/admin/users?q=" + encodeURIComponent(em), { headers: { Authorization: "Bearer " + t } }); return r.json(); }, email);
  const u = (st.users || st).find((x) => x.email === email); ok(u && u.plan_id === "PRO", "user upgraded to PRO by admin: " + (u && u.plan_id));
  ok(errs.length === 0, "no page errors " + errs.join(";")); await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
