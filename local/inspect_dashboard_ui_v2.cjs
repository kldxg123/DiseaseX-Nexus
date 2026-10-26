const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:18130/forecast-model-knowledge-graph', { waitUntil: 'domcontentloaded', timeout: 30000 });
  const result = await page.evaluate(() => ({
    title: document.title,
    url: location.href,
    inputs: [...document.querySelectorAll('input')].map((el) => ({
      type: el.type,
      name: el.name,
      id: el.id,
      placeholder: el.placeholder,
    })),
    buttons: [...document.querySelectorAll('button')].map((el) => el.textContent?.trim()).filter(Boolean).slice(0, 20),
    headings: [...document.querySelectorAll('h1,h2')].map((el) => el.textContent?.trim()).filter(Boolean).slice(0, 20),
  }));
  console.log(JSON.stringify(result, null, 2));
  await context.close();
  await browser.close();
})();
