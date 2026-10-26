const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const [username, password] = fs.readFileSync(
    'F:\\作业\\aibola\\secrets\\dashboard_login.txt',
    'utf8',
  ).replace(/^\uFEFF/, '').split(/\r?\n/);
  if (!username || !password) throw new Error('credential file format invalid');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1800, height: 1200 } });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:18130/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForLoadState('networkidle');
  await page.goto('http://127.0.0.1:18130/forecast-model-knowledge-graph', { waitUntil: 'networkidle', timeout: 60000 });
  const result = await page.evaluate(() => ({
    title: document.title,
    url: location.href,
    headings: [...document.querySelectorAll('h1,h2,h3')].map((el) => el.textContent?.trim()).filter(Boolean),
    buttons: [...document.querySelectorAll('button')].map((el) => el.textContent?.trim()).filter(Boolean),
    selects: [...document.querySelectorAll('select')].map((el) => ({
      id: el.id,
      name: el.name,
      options: [...el.options].map((o) => o.textContent?.trim()).filter(Boolean).slice(0, 20),
    })),
    languageControls: [...document.querySelectorAll('[lang], [data-lang], [aria-label*=language i], [aria-label*=语言]')]
      .map((el) => ({tag: el.tagName, text: el.textContent?.trim(), lang: el.getAttribute('lang'), dataLang: el.getAttribute('data-lang')}))
      .slice(0, 30),
  }));
  console.log(JSON.stringify(result, null, 2));
  await context.close();
  await browser.close();
})();
