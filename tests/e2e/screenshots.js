// Captures every main view (desktop + mobile, light + dark) into tests/screenshots/ for visual review.
const fs = require('fs');
const path = require('path');
const { serve, launch, mock, jwt, signedIn, dev } = require('./helpers');

const OUT = path.resolve(__dirname, '..', 'screenshots');

const ITEMS = [
  dev({ deviceId: 'a1b2c3d4', name: 'Dell Inspiron laptop', status: 'SCHEDULED', category: 'REPAIR', condition: 'needs repair', slot: '2026-10-21 10:00-13:00', address: '14 Lake View Road, Pune 411001',
        history: [{ s: 'REGISTERED', t: 1760000000 }, { s: 'PICKUP_REQUESTED', t: 1760000100 }, { s: 'SCHEDULED', t: 1760000200 }] }),
  dev({ deviceId: 'b2c3d4e5', name: 'iPhone 8', type: 'phone', status: 'REGISTERED', category: 'REUSE' }),
  dev({ deviceId: 'c3d4e5f6', name: 'Old router', type: 'appliance', condition: 'broken', status: 'COLLECTED', category: 'RECYCLE', address: '9 MG Road, Pune' }),
  dev({ deviceId: 'd4e5f6a7', name: 'Samsung tablet', type: 'tablet', status: 'REUSED', category: 'REUSE' }),
  dev({ deviceId: 'e5f6a7b8', name: 'Lenovo ThinkPad', status: 'IN_REPAIR', category: 'REPAIR', condition: 'needs repair' }),
];
const VIEWS = [
  ['landing', null, '#'],
  ['auth-customer', null, '#customer'],
  ['auth-staff', null, '#staff'],
  ['customer', { }, null, 'user'],
  ['collector', { groups: ['collectors'] }, null, 'collectors'],
  ['repairer', { groups: ['repairers'] }, null, 'repairers'],
  ['processor', { groups: ['processors'] }, null, 'processors'],
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { server, url } = await serve();
  const browser = await launch();
  for (const scheme of ['light', 'dark']) {
    for (const [vw, vh, tag] of [[1280, 900, 'desktop'], [390, 844, 'mobile']]) {
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, colorScheme: scheme });
      for (const [name, tok, hash, role] of VIEWS) {
        const page = await ctx.newPage();
        await mock(page, { api: (m, p) => (m === 'GET' && p === '/devices' ? { json: { items: ITEMS, role } } : { json: {} }) });
        if (tok) { await signedIn(page, url, jwt({ email: 'riya.sharma@example.com', ...tok })); await page.waitForSelector('.page-head'); }
        else { await page.goto(url + (hash || '')); await page.waitForTimeout(2800); }
        if (name === 'customer') { await page.evaluate(() => document.querySelector('details.track')?.setAttribute('open', '')); }
        await page.screenshot({ path: path.join(OUT, `${name}-${tag}-${scheme}.png`), fullPage: true });
        await page.close();
      }
      await ctx.close();
    }
  }
  await browser.close(); server.close();
  console.log('Screenshots written to', OUT);
})();
