// Browser tests for the newer features: slot booking changes, failed pickups, the marketplace, staff alerts.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { serve, launch, mock, jwt, signedIn, dev } = require('./helpers');

let srv, browser, base;
before(async () => { srv = await serve(); base = srv.url; browser = await launch(); });
after(async () => { await browser.close(); srv.server.close(); });

async function newPage(opts = {}, viewport = { width: 1280, height: 900 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const calls = await mock(page, opts);
  return { page, calls, close: () => ctx.close() };
}
const listApi = (items, role = 'user') => (m, p) => (m === 'GET' && p === '/devices' ? { json: { items, role } } : { json: {} });
const FUTURE = '2099-01-05 09:00-12:00';
const SLOT_LIST = { slots: [{ slot: '2099-01-06 09:00-12:00', left: 2 }, { slot: '2099-01-06 12:00-15:00', left: 1 }] };
const toastSeen = (page, re) => page.waitForFunction(src => new RegExp(src).test(document.getElementById('toast').textContent), re.source);

test('a scheduled pickup can be rescheduled to a slot chosen from the available list', async () => {
  const t = await newPage({ api: (m, p) => (m === 'GET' && p === '/devices' ? { json: { items: [dev({ status: 'SCHEDULED', slot: FUTURE })], role: 'user' } } : m === 'GET' && p === '/slots' ? { json: SLOT_LIST } : { json: { slot: 'x' } }) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Reschedule' }).click();
  const sel = t.page.getByLabel('Available slots');
  const opts = await sel.locator('option').allInnerTexts();
  assert.equal(opts.length, 2);
  assert.match(opts[0], /6.*09:00 to 12:00 \(2 left\)/);   // the day wording follows the browser locale
  assert.match(opts[1], /6.*12:00 to 15:00 \(1 left\)/);
  await sel.selectOption('2099-01-06 12:00-15:00');
  await t.page.getByRole('dialog').getByRole('button', { name: 'Reschedule' }).click();
  await t.page.getByRole('dialog').waitFor({ state: 'hidden' });
  const c = t.calls.find(x => x.method === 'POST' && x.path === '/devices/abcdef123456/reschedule');
  assert.deepEqual(c.body, { slot: '2099-01-06 12:00-15:00' });
  await t.close();
});

test('cancelling a pickup asks first, then posts the cancellation', async () => {
  const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status: 'SCHEDULED', slot: FUTURE })], role: 'user' } } : { json: { message: 'pickup cancelled' } }) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Cancel pickup' }).click();
  await t.page.getByRole('button', { name: 'Keep it' }).click();
  assert.equal(t.calls.filter(x => x.path && x.path.endsWith('/cancel')).length, 0, 'keeping it sends nothing');
  await t.page.getByRole('button', { name: 'Cancel pickup' }).click();
  await t.page.getByRole('dialog').getByRole('button', { name: 'Cancel pickup' }).click();
  await toastSeen(t.page, /cancelled/);
  assert.ok(t.calls.some(x => x.method === 'POST' && x.path === '/devices/abcdef123456/cancel'));
  await t.close();
});

test('a failed pickup shows its reason and lets the customer request another slot', async () => {
  const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status: 'PICKUP_FAILED', address: '1 Main St', lastNote: 'Customer was not available', failedAttempts: 1 })], role: 'user' } } : { status: 202, json: {} }) });
  await signedIn(t.page, base, jwt());
  assert.match(await t.page.locator('.trow').first().innerText(), /Pickup failed: Customer was not available \(attempt 1 of 3\)/);
  await t.page.getByRole('button', { name: 'Request another slot' }).click();
  assert.equal(await t.page.getByLabel('Pickup address').inputValue(), '1 Main St', 'the saved address is pre-filled');
  await t.page.getByRole('button', { name: 'Request pickup' }).click();
  await t.page.getByRole('dialog').waitFor({ state: 'hidden' });
  const c = t.calls.find(x => x.method === 'POST' && x.path === '/devices/abcdef123456/pickup');
  assert.deepEqual(c.body, { address: '1 Main St' });
  await t.close();
});

test('a collector records a failed collection with a reason', async () => {
  const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status: 'SCHEDULED', address: '1 Main St', slot: FUTURE })], role: 'collectors' } } : { json: { message: 'updated' } }) });
  await signedIn(t.page, base, jwt({ groups: ['collectors'] }));
  await t.page.getByRole('button', { name: 'Collection failed' }).click();
  await t.page.getByLabel('Reason').selectOption('Wrong or incomplete address');
  await t.page.getByLabel('Details (optional)').fill('no such house number');
  await t.page.getByRole('button', { name: 'Record failed pickup' }).click();
  await t.page.getByRole('dialog').waitFor({ state: 'hidden' });
  const patch = t.calls.find(c => c.method === 'PATCH');
  assert.deepEqual(patch.body, { status: 'PICKUP_FAILED', note: 'Wrong or incomplete address: no such house number' });
  await t.close();
});

const MARKET = [
  { deviceId: 'm1', name: 'Refurbished ThinkPad', type: 'laptop', condition: 'working', age: 4, category: 'REUSE', status: 'LISTED', mine: false },
  { deviceId: 'm2', name: 'Old iPhone', type: 'phone', condition: 'working', age: 5, category: 'REUSE', status: 'RESERVED', mine: true }];
const marketApi = (m, p) => (m === 'GET' && p === '/market' ? { json: { items: MARKET } } : m === 'GET' ? { json: { items: [], role: 'user' } } : { json: { message: 'ok' } });

test('the marketplace lists items, shows no owner details, and reserves and releases', async () => {
  const t = await newPage({ api: marketApi });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('tab', { name: 'Reuse marketplace' }).click();
  await t.page.getByRole('heading', { name: 'Reuse marketplace' }).waitFor();
  const text = await t.page.locator('#dash').innerText();
  assert.ok(text.includes('Refurbished ThinkPad') && text.includes('Old iPhone'));
  assert.doesNotMatch(text, /user-1|address/i, 'no owner information is shown');
  await t.page.getByRole('button', { name: 'Reserve' }).click();
  await toastSeen(t.page, /Reserved\./);
  assert.ok(t.calls.some(c => c.method === 'POST' && c.path === '/market/m1/reserve'));
  await t.page.getByRole('button', { name: 'Release' }).click();
  await toastSeen(t.page, /released/);
  assert.ok(t.calls.some(c => c.method === 'POST' && c.path === '/market/m2/release'));
  await t.close();
});

test('market filters are sent to the API as query parameters', async () => {
  const t = await newPage({ api: marketApi });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('tab', { name: 'Reuse marketplace' }).click();
  await t.page.getByRole('heading', { name: 'Reuse marketplace' }).waitFor();
  const urls = [];
  t.page.on('request', r => urls.push(r.url()));
  await t.page.getByLabel('Condition').selectOption('needs repair');
  await t.page.waitForFunction(() => document.querySelector('#fCond')?.value === 'needs repair');
  await t.page.getByLabel('Type').selectOption('laptop');
  await t.page.waitForFunction(() => document.querySelector('#fType')?.value === 'laptop');
  await t.page.waitForTimeout(300);
  assert.ok(urls.some(u => /\/market\?/.test(u) && u.includes('type=laptop') && u.includes('condition=needs+repair')), urls.join(', '));
  await t.close();
});

const PROC = [
  ['PROCESSING', 'REUSE', 'List on marketplace', 'LISTED'], ['LISTED', 'REUSE', 'Delist', 'PROCESSING'],
  ['RESERVED', 'REUSE', 'Confirm handover', 'REUSED'], ['RESERVED', 'REUSE', 'Release reservation', 'LISTED']];
for (const [status, cat, button, expected] of PROC) {
  test(`processors: "${button}" on a ${status} device sends ${expected}`, async () => {
    const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status, category: cat })], role: 'processors' } } : { json: { message: 'updated' } }) });
    await signedIn(t.page, base, jwt({ groups: ['processors'] }));
    await t.page.getByRole('button', { name: button }).click();
    await toastSeen(t.page, /Updated to/);
    assert.deepEqual(t.calls.find(c => c.method === 'PATCH').body, { status: expected });
    await t.close();
  });
}

test('recycle devices cannot be listed on the marketplace from the UI', async () => {
  const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status: 'PROCESSING', category: 'RECYCLE' })], role: 'processors' } } : { json: {} }) });
  await signedIn(t.page, base, jwt({ groups: ['processors'] }));
  await t.page.getByRole('button', { name: 'Mark recycled' }).waitFor();
  assert.equal(await t.page.getByRole('button', { name: 'List on marketplace' }).count(), 0);
  await t.close();
});

test('staff subscribe to new-task emails and customers to their own updates', async () => {
  for (const [groups, label] of [[['collectors'], 'Email me new tasks'], [undefined, 'Email me updates']]) {
    const t = await newPage({ api: (m, p) => (p === '/subscribe' ? { json: { message: 'Check your inbox and confirm the subscription email' } } : { json: { items: [], role: groups ? 'collectors' : 'user' } }) });
    await signedIn(t.page, base, jwt({ groups }));
    await t.page.getByRole('button', { name: label }).click();
    await toastSeen(t.page, /confirm the subscription/);
    assert.ok(t.calls.some(c => c.method === 'POST' && c.path === '/subscribe'));
    await t.close();
  }
});

test('the history names the role behind each step, notes appear, and the triage line shows the override', async () => {
  const d = dev({ status: 'COLLECTED', category: 'RECYCLE', assessment: { recommended: 'REPAIR', reason: 'x' }, history: [
    { s: 'REGISTERED', t: 1760000000, r: 'user' }, { s: 'SCHEDULED', t: 1760000100, r: 'system' }, { s: 'COLLECTED', t: 1760000200, r: 'collectors', n: 'picked up at gate' }] });
  const t = await newPage({ api: listApi([d]) });
  await signedIn(t.page, base, jwt());
  await t.page.locator('details.track summary').click();
  const h = await t.page.locator('.hist').innerText();
  assert.match(h, /Registered.*by customer/);
  assert.match(h, /Pickup scheduled.*by system/);
  assert.match(h, /Collected.*by collector.*picked up at gate/);
  assert.match(await t.page.locator('.trow').first().innerText(), /Triage: Repair \(chosen: Recycle\)/i);
  await t.close();
});

test('new screens do not scroll sideways on a phone', async () => {
  for (const [token, api, tab] of [[jwt(), marketApi, 'Reuse marketplace'], [jwt({ groups: ['processors'] }), (m) => (m === 'GET' ? { json: { items: [dev({ status: 'LISTED' }), dev({ deviceId: 'zz', status: 'RESERVED', name: 'A very long device name that keeps going and going to stress the layout' })], role: 'processors' } } : { json: {} }), null]]) {
    const t = await newPage({ api }, { width: 360, height: 740 });
    await signedIn(t.page, base, token);
    if (tab) await t.page.getByRole('tab', { name: tab }).click();
    await t.page.waitForTimeout(400);
    const over = await t.page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(over <= 1, `page is ${over}px wider than the viewport`);
    await t.close();
  }
});
