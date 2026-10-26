const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const [username, password] = fs.readFileSync('F:\\作业\\aibola\\secrets\\dashboard_login.txt', 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:18130/login', { waitUntil: 'domcontentloaded' });
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForLoadState('networkidle');
  await page.goto('http://127.0.0.1:18130/forecast-model-knowledge-graph', { waitUntil: 'networkidle', timeout: 60000 });
  await page.locator('#kgQTrackC').click();
  await page.waitForTimeout(1800);
  const ids = ['kgQProfile','kgQRec','kgQRounds','kgQEval','kgQPaired','kgQChampion','kg-paper-benchmark-e'];
  const out = {};
  for (const id of ids) {
    const loc = page.locator('#' + id);
    out[id] = await loc.count() ? (await loc.innerText()).replace(/\s+/g, ' ').trim() : null;
  }
  console.log(JSON.stringify(out, null, 2));
  await context.close();
  await browser.close();
})();
