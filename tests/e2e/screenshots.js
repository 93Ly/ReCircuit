// Captures every main view (desktop + mobile, light + dark) into tests/screenshots/ for visual review.
const fs = require('fs');
const path = require('path');
const { serve, launch, mock, jwt, signedIn, dev } = require('./helpers');

const OUT = path.resolve(__dirname, '..', 'screenshots');
const SLOT = '2099-01-06 09:00-12:00';
const ITEMS = [
  dev({ deviceId: 'a1b2c3d4', name: 'Dell Inspiron laptop', status: 'SCHEDULED', category: 'REPAIR', condition: 'needs repair', age: 2, slot: SLOT, address: '14 Lake View Road, Pune 411001',
        assessment: { recommended: 'REPAIR', reason: 'Repairable and at most 6 years old: worth repairing' },
        history: [{ s: 'REGISTERED', t: 1760000000, r: 'user' }, { s: 'PICKUP_REQUESTED', t: 1760000100, r: 'user' }, { s: 'SCHEDULED', t: 1760000200, r: 'system' }] }),
  dev({ deviceId: 'b2c3d4e5', name: 'iPhone 8', type: 'phone', status: 'REGISTERED', category: 'REUSE', age: 6 }),
  dev({ deviceId: 'c3d4e5f6', name: 'Old router', type: 'appliance', condition: 'broken', status: 'PICKUP_FAILED', category: 'RECYCLE', age: 11, address: '9 MG Road, Pune',
        lastNote: 'Customer was not available', failedAttempts: 1, assessment: { recommended: 'RECYCLE', reason: 'Broken and older than 3 years: recycle' } }),
  dev({ deviceId: 'd4e5f6a7', name: 'Samsung tablet', type: 'tablet', status: 'LISTED', category: 'REUSE', age: 4 }),
  dev({ deviceId: 'e5f6a7b8', name: 'Lenovo ThinkPad', status: 'PROCESSING', category: 'REUSE', age: 5 }),
  dev({ deviceId: 'f6a7b8c9', name: 'HP Pavilion', status: 'RESERVED', category: 'REUSE', age: 3 }),
];
const MARKET = [
  { deviceId: 'm1a2b3c4', name: 'Refurbished ThinkPad T480', type: 'laptop', condition: 'working', age: 5, category: 'REUSE', status: 'LISTED', mine: false },
  { deviceId: 'm2b3c4d5', name: 'iPhone SE (2020)', type: 'phone', condition: 'working', age: 4, category: 'REUSE', status: 'RESERVED', mine: true },
  { deviceId: 'm3c4d5e6', name: 'Galaxy Tab A', type: 'tablet', condition: 'needs repair', age: 3, category: 'REPAIR', status: 'LISTED', mine: false },
];
const api = role => (m, p, b) => {
  if (m === 'GET' && p === '/devices') return { json: { items: ITEMS, role } };
  if (m === 'GET' && p === '/market') return { json: { items: MARKET } };
  if (m === 'GET' && p === '/slots') return { json: { slots: [{ slot: SLOT, left: 2 }, { slot: '2099-01-06 12:00-15:00', left: 1 }, { slot: '2099-01-07 09:00-12:00', left: 3 }] } };
  if (m === 'POST' && p === '/assess') return { json: { recommended: 'REPAIR', reason: 'Repairable and at most 6 years old: worth repairing' } };
  return { json: {} };
};
// [name, token options, hash, role, optional step run before the screenshot]
const VIEWS = [
  ['landing', null, '#'],
  ['auth-customer', null, '#customer'],
  ['auth-staff', null, '#staff'],
  ['customer', {}, null, 'user', async p => { await p.evaluate(() => document.querySelector('details.track')?.setAttribute('open', '')); }],
  ['market', {}, null, 'user', async p => { await p.getByRole('tab', { name: 'Reuse marketplace' }).click(); await p.getByRole('heading', { name: 'Reuse marketplace' }).waitFor(); await p.waitForTimeout(200); }],
  ['register', {}, null, 'user', async p => { await p.getByRole('button', { name: 'Register a device' }).click(); await p.locator('#triage b').waitFor(); }],
  ['reschedule', {}, null, 'user', async p => { await p.getByRole('button', { name: 'Reschedule' }).click(); await p.getByLabel('Available slots').waitFor(); }],
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
      for (const [name, tok, hash, role, step] of VIEWS) {
        const page = await ctx.newPage();
        await mock(page, { api: api(role) });
        if (tok) { await signedIn(page, url, jwt({ email: 'riya.sharma@example.com', ...tok })); await page.waitForSelector('.page-head'); }
        else { await page.goto(url + (hash || '')); await page.waitForTimeout(2800); }
        if (step) await step(page);
        await page.screenshot({ path: path.join(OUT, `${name}-${tag}-${scheme}.png`), fullPage: !['register', 'reschedule'].includes(name) });
        await page.close();
      }
      await ctx.close();
    }
  }
  await browser.close(); server.close();
  console.log('Screenshots written to', OUT);
})();
