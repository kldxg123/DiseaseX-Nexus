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
  const sections = await page.evaluate(() => [...document.querySelectorAll('[id]')].map((el) => {
    const r = el.getBoundingClientRect();
    return {id: el.id, tag: el.tagName, cls: el.className, w: Math.round(r.width), h: Math.round(r.height), text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 220)};
  }).filter((x) => /kg-|paper|qwen|param|benchmark|loop|evaluation/i.test(x.id) || /评估|回测|预测/i.test(x.text)).slice(0, 180));
  console.log(JSON.stringify(sections, null, 2));
  await context.close();
  await browser.close();
})();
