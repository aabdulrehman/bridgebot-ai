// Headless-Chrome UI smoke test. Needs: backend serving the PWA on :8000, `npm i puppeteer-core`, Chrome at /usr/bin/google-chrome.
// Run from repo root: node tests/ui_smoke.js
const puppeteer = require('puppeteer-core');
const { spawn } = require('child_process');
const B = 'http://localhost:8000';
const j = async (path, body, token, method='POST') => (await fetch(B+path, {method, headers: {'Content-Type':'application/json', ...(token?{Authorization:'Bearer '+token}:{})}, body: body?JSON.stringify(body):undefined})).json();
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true });
  page.on('pageerror', e => console.log('PAGEERROR', e.message));
  // Register through the real UI
  await page.goto(B + '/login/', { waitUntil: 'networkidle0' });
  await page.click('button[type=button]:nth-of-type(2)');
  await page.type('#email', 'ui@example.com'); await page.type('#password', 'UiPassword123');
  await page.click('button.btn-primary');
  await page.waitForFunction(() => location.pathname.startsWith('/dashboard'), { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('bridgebot_token'));
  console.log('registered via UI, token len', token.length);
  // Add machine through UI
  await page.waitForSelector('#machine');
  await page.type('#machine', 'home-pc'); 
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.trim()==='Add').click());
  await page.waitForFunction(() => document.body.innerText.includes('shown only once'), { timeout: 10000 });
  const key = await page.evaluate(() => document.querySelector('code').textContent);
  console.log('key from UI:', key.slice(0, 10) + '…');
  await page.screenshot({ path: '/workspace/bridgebot/docs/screenshot-add-machine.png', fullPage: true });
  const conn = spawn('/workspace/bridgebot/.venv/bin/python', ['/workspace/bridgebot/connector/local_connector.py', '--config', '/nonexistent.json', '--server', B, '--api-key', key, '--machine-name', 'home-pc', '--skills-dir', '/tmp/ui_skills'], { stdio: 'ignore' });
  await page.waitForFunction(() => document.body.innerText.includes('home-pc is ONLINE'), { timeout: 15000 });
  console.log('dashboard saw ONLINE');
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('I saved it')).click());
  await sleep(1200);
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('Run')).click());
  await page.waitForFunction(() => document.body.innerText.includes('Hello from BridgeBot') && document.body.innerText.includes('exit 0'), { timeout: 10000 });
  const input = await page.$('input.font-mono'); await input.click({ clickCount: 3 }); await input.type('ls -la /workspace/bridgebot');
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('Run')).click());
  await sleep(1500);
  console.log('console lines:', await page.evaluate(() => document.body.innerText.match(/Live console · (\d+)/)[1]));
  await page.screenshot({ path: '/workspace/bridgebot/docs/screenshot-dashboard.png', fullPage: true });
  // Train flow via UI
  await page.goto(B + '/train/', { waitUntil: 'networkidle0' });
  const next = () => page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('Next')).click());
  await page.type('#biz', 'Desert Tents Co'); await next();
  await page.type('#niche', 'We sell and rent camping tents and heaters in Riyadh; answer WhatsApp leads, quote prices, book deliveries.'); await next();
  await page.type('#goal', 'quote tent prices'); await page.keyboard.press('Enter'); await next();
  await sleep(300); await next();
  await page.screenshot({ path: '/workspace/bridgebot/docs/screenshot-train-review.png', fullPage: true });
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('Compile skill')).click());
  await page.waitForFunction(() => document.body.innerText.includes('Approve & inject'), { timeout: 20000 });
  console.log('preview READY in UI');
  await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('Approve & inject')).click());
  await page.waitForFunction(() => document.body.innerText.includes('Skill installed on your machine'), { timeout: 15000 });
  console.log('UI got SKILL_ACK:', await page.evaluate(() => document.body.innerText.match(/Skill installed on your machine at (.*)/)[1]));
  await page.screenshot({ path: '/workspace/bridgebot/docs/screenshot-train-approved.png', fullPage: true });
  conn.kill();
  await browser.close();
  console.log('UI TEST PASSED');
})().catch(e => { console.error('UI TEST FAILED', e); process.exit(1); });
