// Browser tests for the ReCircuit frontend. Run with: npm test (from /tests) or node --test e2e/
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { serve, launch, mock, jwt, signedIn, dev } = require('./helpers');

let srv, browser, base;
before(async () => { srv = await serve(); base = srv.url; browser = await launch(); });
after(async () => { await browser.close(); srv.server.close(); });

async function newPage(opts = {}, viewport = { width: 1280, height: 900 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  const calls = await mock(page, opts);
  return { page, calls, errors, close: () => ctx.close() };
}
const listApi = (items, role = 'user') => (m, p) => (m === 'GET' && p === '/devices' ? { json: { items, role } } : { json: {} });
const auth = token => ({ json: { AuthenticationResult: { IdToken: token } } });

// ---------------- landing and navigation ----------------
test('landing page shows the story, the cited facts and both portals', async () => {
  const t = await newPage();
  await t.page.goto(base);
  assert.equal(await t.page.title(), 'ReCircuit');
  assert.match(await t.page.locator('h1').first().innerText(), /Route it\./);
  const body = await t.page.locator('body').innerText();
  for (const s of ['62 Mt', '22.3%', '82 Mt', 'Customers', 'Collectors', 'Repair technicians', 'Processors']) assert.ok(body.includes(s), 'missing ' + s);
  const href = await t.page.getByRole('link', { name: /UN Global E-waste Monitor 2024/ }).getAttribute('href');
  assert.match(href, /^https:\/\/www\.unitar\.org\//);
  assert.deepEqual(t.errors, []);
  await t.close();
});

test('the front page has one Sign in button and no staff entrance', async () => {
  const t = await newPage();
  await t.page.goto(base);
  assert.equal(await t.page.locator('#landing a[href="#staff"]').count(), 0, 'no staff link on the landing page');
  assert.equal(await t.page.locator('#landing a[href="#customer"]').count(), 0);
  const signIn = t.page.locator('#landing a[href="#signin"]');
  assert.ok((await signIn.count()) >= 1);
  assert.doesNotMatch(await t.page.locator('#landing').innerText(), /staff (sign|portal|login)/i);
  await signIn.first().click();
  await t.page.getByRole('heading', { name: 'Sign in' }).waitFor();
  const tabs = t.page.getByRole('tab');
  assert.deepEqual(await tabs.allInnerTexts(), ['Customer', 'Staff']);
  assert.equal(await t.page.getByRole('tab', { name: 'Customer' }).getAttribute('aria-selected'), 'true');
  assert.ok(await t.page.getByRole('button', { name: 'Create an account' }).isVisible());
  await t.page.getByRole('tab', { name: 'Staff' }).click();
  await t.page.waitForFunction(() => /queue/i.test(document.getElementById('authHeadline').textContent));
  assert.equal(await t.page.getByRole('tab', { name: 'Staff' }).getAttribute('aria-selected'), 'true');
  assert.equal(await t.page.getByRole('button', { name: 'Create an account' }).count(), 0, 'staff cannot self-register');
  await t.close();
});

test('customer and staff portals are separate screens', async () => {
  const t = await newPage();
  await t.page.goto(base + '#customer');
  assert.equal(await t.page.locator('#formCard .eyebrow').textContent(), 'Customer portal');
  assert.ok(await t.page.getByRole('button', { name: 'Create an account' }).isVisible());
  await t.page.goto(base + '#staff');
  assert.equal(await t.page.locator('#formCard .eyebrow').textContent(), 'Staff portal');
  assert.equal(await t.page.getByRole('button', { name: 'Create an account' }).count(), 0, 'staff cannot self-register');
  assert.match(await t.page.locator('#authHeadline').innerText(), /queue/i);
  await t.close();
});

// ---------------- authentication ----------------
test('sign-up then email code then back to sign-in', async () => {
  const t = await newPage({ cognito: () => ({ json: {} }) });
  await t.page.goto(base + '#customer');
  await t.page.getByRole('button', { name: 'Create an account' }).click();
  await t.page.getByLabel('Email').fill('new@user.com');
  await t.page.getByLabel('Password').fill('abcdefg1');
  await t.page.getByRole('button', { name: 'Create account' }).click();
  await t.page.getByLabel('Verification code').fill('123456');
  await t.page.getByRole('button', { name: /Verify/ }).click();
  await t.page.getByRole('heading', { name: 'Sign in' }).waitFor();
  const su = t.calls.find(c => c.target === 'SignUp').body;
  assert.equal(su.Username, 'new@user.com');
  assert.equal(su.Password, 'abcdefg1');
  assert.deepEqual(su.UserAttributes, [{ Name: 'email', Value: 'new@user.com' }]);
  const cf = t.calls.find(c => c.target === 'ConfirmSignUp').body;
  assert.equal(cf.ConfirmationCode, '123456');
  assert.match(await t.page.locator('#toast').innerText(), /verified/i);
  await t.close();
});

test('wrong password shows a friendly error and no session', async () => {
  const t = await newPage({ cognito: () => ({ status: 400, json: { __type: 'NotAuthorizedException', message: 'Incorrect username or password.' } }) });
  await t.page.goto(base + '#customer');
  await t.page.getByLabel('Email').fill('a@b.com');
  await t.page.getByLabel('Password').fill('wrongpass1');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.locator('#err').waitFor();
  assert.equal(await t.page.locator('#err').innerText(), 'Incorrect email or password.');
  assert.equal(await t.page.evaluate(() => sessionStorage.getItem('idt')), null);
  await t.close();
});

test('unconfirmed account is sent to the code screen and a new code is requested', async () => {
  const t = await newPage({ cognito: tg => (tg === 'InitiateAuth' ? { status: 400, json: { __type: 'UserNotConfirmedException', message: 'x' } } : { json: {} }) });
  await t.page.goto(base + '#customer');
  await t.page.getByLabel('Email').fill('late@user.com');
  await t.page.getByLabel('Password').fill('abcdefg1');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.getByLabel('Verification code').waitFor();
  await t.page.waitForTimeout(200);
  assert.ok(t.calls.some(c => c.target === 'ResendConfirmationCode' && c.body.Username === 'late@user.com'));
  await t.close();
});

test('customer signs in and lands on their dashboard with the token on API calls', async () => {
  const token = jwt({ email: 'riya@example.com' });
  const t = await newPage({ cognito: () => auth(token), api: listApi([dev({ status: 'REUSED' }), dev({ deviceId: 'zz9988776655', name: 'Phone' })]) });
  await t.page.goto(base + '#customer');
  await t.page.getByLabel('Email').fill('riya@example.com');
  await t.page.getByLabel('Password').fill('abcdefg1');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.getByRole('heading', { name: 'Your devices' }).waitFor();
  assert.equal(await t.page.locator('#roleTag').textContent(), 'Customer');
  assert.equal(await t.page.locator('#email').innerText(), 'riya@example.com');
  const counters = await t.page.locator('.counter b').allInnerTexts();
  assert.deepEqual(counters, ['2', '1', '1']);
  const get = t.calls.find(c => c.kind === 'api' && c.method === 'GET');
  assert.equal(get.auth, token);
  await t.close();
});

test('portals reject the wrong kind of account', async () => {
  // staff account on the customer portal
  let t = await newPage({ cognito: () => auth(jwt({ groups: ['collectors'] })) });
  await t.page.goto(base + '#customer');
  await t.page.getByLabel('Email').fill('s@x.com'); await t.page.getByLabel('Password').fill('abcdefg1');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.locator('#err').waitFor();
  assert.match(await t.page.locator('#err').innerText(), /staff account/i);
  assert.equal(await t.page.evaluate(() => sessionStorage.getItem('idt')), null);
  await t.close();
  // customer account on the staff portal
  t = await newPage({ cognito: () => auth(jwt()) });
  await t.page.goto(base + '#staff');
  await t.page.getByLabel('Email').fill('c@x.com'); await t.page.getByLabel('Password').fill('abcdefg1');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.locator('#err').waitFor();
  assert.match(await t.page.locator('#err').innerText(), /no staff role/i);
  assert.equal(await t.page.evaluate(() => sessionStorage.getItem('idt')), null);
  await t.close();
});

test('first sign-in of a staff account forces a new password', async () => {
  const token = jwt({ groups: ['repairers'] });
  const t = await newPage({
    cognito: tg => (tg === 'InitiateAuth' ? { json: { ChallengeName: 'NEW_PASSWORD_REQUIRED', Session: 'sess-1' } } : auth(token)),
    api: listApi([], 'repairers')
  });
  await t.page.goto(base + '#staff');
  await t.page.getByLabel('Email').fill('tech@x.com'); await t.page.getByLabel('Password').fill('Temp1234');
  await t.page.getByRole('button', { name: 'Sign in' }).click();
  await t.page.getByRole('heading', { name: 'Choose a new password' }).waitFor();
  await t.page.getByLabel('New password').fill('Better1234');
  await t.page.getByRole('button', { name: /Save and sign in/ }).click();
  await t.page.getByRole('heading', { name: 'Task queue' }).waitFor();
  const ch = t.calls.find(c => c.target === 'RespondToAuthChallenge').body;
  assert.equal(ch.Session, 'sess-1');
  assert.equal(ch.ChallengeResponses.NEW_PASSWORD, 'Better1234');
  await t.close();
});

// ---------------- customer features ----------------
test('registering a device sends the right payload (with photo upload first)', async () => {
  const t = await newPage({ api: (m, p) => {
    if (m === 'GET') return { json: { items: [], role: 'user' } };
    if (p === '/upload-url') return { json: { uploadUrl: 'https://uploads.example/put', photoKey: 'user-1/photo-key' } };
    return { status: 201, json: {} };
  } });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Register a device' }).click();
  assert.ok(await t.page.getByRole('dialog').isVisible());
  await t.page.getByLabel('Device name').fill('Dell Inspiron');
  await t.page.getByLabel('Type').selectOption('laptop');
  await t.page.getByLabel('Condition').selectOption('needs repair');
  await t.page.getByLabel('What should happen to it?').selectOption('REPAIR');
  await t.page.getByLabel('Photo (optional)').setInputFiles({ name: 'p.png', mimeType: 'image/png', buffer: Buffer.from('89504e47', 'hex') });
  await t.page.getByRole('button', { name: 'Register', exact: true }).click();
  await t.page.getByRole('dialog').waitFor({ state: 'hidden' });
  const up = t.calls.find(c => c.path === '/upload-url');
  assert.equal(up.body.contentType, 'image/png');
  const post = t.calls.find(c => c.method === 'POST' && c.path === '/devices');
  assert.deepEqual(post.body, { name: 'Dell Inspiron', type: 'laptop', condition: 'needs repair', category: 'REPAIR', photoKey: 'user-1/photo-key' });
  assert.ok(t.calls.indexOf(up) < t.calls.indexOf(post), 'upload URL requested before creating the device');
  await t.close();
});

test('pickup needs an address and posts it for the right device', async () => {
  const t = await newPage({ api: listApi([dev()]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Request pickup' }).click();
  const empty = await t.page.getByLabel('Pickup address').evaluate(el => el.checkValidity());
  assert.equal(empty, false, 'empty address must be blocked by the form');
  await t.page.getByLabel('Pickup address').fill('14 Lake View Road, Pune');
  await t.page.getByRole('button', { name: 'Request pickup' }).last().click();
  await t.page.getByRole('dialog').waitFor({ state: 'hidden' });
  const c = t.calls.find(x => x.method === 'POST' && x.path === '/devices/abcdef123456/pickup');
  assert.deepEqual(c.body, { address: '14 Lake View Road, Pune' });
  await t.close();
});

test('only registered devices offer a pickup button; tracker marks the right step', async () => {
  const t = await newPage({ api: listApi([dev({ name: 'A', status: 'REGISTERED' }), dev({ deviceId: 'b2', name: 'B', status: 'IN_REPAIR', category: 'REPAIR' })]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('heading', { name: 'Your devices' }).waitFor();
  assert.equal(await t.page.getByRole('button', { name: 'Request pickup' }).count(), 1);
  await t.page.locator('details.track').nth(1).locator('summary').click();
  const now = await t.page.locator('details.track[open] .tracker li.now').innerText();
  assert.equal(now.toLowerCase(), 'in repair');
  assert.equal(await t.page.locator('details.track[open] .tracker li.done').count(), 4);
  await t.close();
});

test('an opened tracker stays open when the list refreshes', async () => {
  const t = await newPage({ api: listApi([dev()]) });
  await signedIn(t.page, base, jwt());
  await t.page.locator('details.track summary').click();
  await t.page.evaluate(() => load());
  await t.page.waitForTimeout(300);
  assert.equal(await t.page.locator('details.track').getAttribute('open') !== null, true);
  await t.close();
});

test('customers cannot see staff controls and staff cannot see customer controls', async () => {
  let t = await newPage({ api: listApi([dev()]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('heading', { name: 'Your devices' }).waitFor();
  assert.equal(await t.page.locator('[data-to]').count(), 0);
  assert.ok(await t.page.getByRole('button', { name: 'Email me updates' }).isVisible());
  await t.close();
  t = await newPage({ api: listApi([dev({ status: 'SCHEDULED', address: '1 Main St' })], 'collectors') });
  await signedIn(t.page, base, jwt({ groups: ['collectors'] }));
  await t.page.getByRole('heading', { name: 'Task queue' }).waitFor();
  assert.equal(await t.page.getByRole('button', { name: 'Register a device' }).count(), 0);
  assert.equal(await t.page.getByRole('button', { name: 'Email me updates' }).isHidden(), true);
  await t.close();
});

// ---------------- staff workflows ----------------
const WORKFLOWS = [
  ['collectors', 'SCHEDULED', 'Mark collected', 'COLLECTED', { address: '1 Main St, Pune' }],
  ['collectors', 'REPAIRED', 'Mark returned', 'RETURNED', { address: '1 Main St, Pune' }],
  ['repairers', 'COLLECTED', 'Start repair', 'IN_REPAIR', { category: 'REPAIR' }],
  ['repairers', 'IN_REPAIR', 'Mark repaired', 'REPAIRED', { category: 'REPAIR' }],
  ['repairers', 'IN_REPAIR', 'Cannot repair', 'UNREPAIRABLE', { category: 'REPAIR' }],
  ['processors', 'COLLECTED', 'Start processing', 'PROCESSING', { category: 'RECYCLE' }],
  ['processors', 'UNREPAIRABLE', 'Start processing', 'PROCESSING', { category: 'REPAIR' }],
  ['processors', 'PROCESSING', 'Mark reused', 'REUSED', { category: 'REUSE' }],
  ['processors', 'PROCESSING', 'Mark recycled', 'RECYCLED', { category: 'RECYCLE' }],
];
for (const [role, status, button, expected, extra] of WORKFLOWS) {
  test(`${role}: "${button}" on a ${status} device sends status ${expected}`, async () => {
    const t = await newPage({ api: (m, p) => (m === 'GET' ? { json: { items: [dev({ status, ...extra })], role } } : { json: { message: 'updated' } }) });
    await signedIn(t.page, base, jwt({ groups: [role] }));
    await t.page.getByRole('button', { name: button }).click();
    await t.page.waitForFunction(() => /Updated to/.test(document.getElementById('toast').textContent));
    const patch = t.calls.find(c => c.method === 'PATCH');
    assert.equal(patch.path, '/devices/abcdef123456/status');
    assert.deepEqual(patch.body, { status: expected });
    await t.close();
  });
}

test('collector sees the customer address; the other staff roles are never shown it', async () => {
  const item = dev({ status: 'SCHEDULED', address: '221B Baker Street', slot: '2026-10-21 10:00-13:00' });
  let t = await newPage({ api: listApi([item], 'collectors') });
  await signedIn(t.page, base, jwt({ groups: ['collectors'] }));
  await t.page.getByText('221B Baker Street').waitFor();
  await t.close();
  // the backend strips address for these roles; the UI only renders it in "addr" sections (collector only)
  t = await newPage({ api: listApi([{ ...item, status: 'COLLECTED', category: 'REPAIR' }], 'repairers') });
  await signedIn(t.page, base, jwt({ groups: ['repairers'] }));
  await t.page.getByRole('heading', { name: 'Task queue' }).waitFor();
  assert.equal(await t.page.getByText('221B Baker Street').count(), 0);
  await t.close();
});

test('API errors from a status change are shown and the button is re-enabled', async () => {
  const t = await newPage({ api: (m) => (m === 'GET' ? { json: { items: [dev({ status: 'SCHEDULED' })], role: 'collectors' } } : { status: 403, json: { error: 'only collectors can do this step' } }) });
  await signedIn(t.page, base, jwt({ groups: ['collectors'] }));
  await t.page.getByRole('button', { name: 'Mark collected' }).click();
  await t.page.waitForFunction(() => /only collectors/.test(document.getElementById('toast').textContent));
  assert.equal(await t.page.getByRole('button', { name: 'Mark collected' }).isEnabled(), true);
  await t.close();
});

// ---------------- sessions and safety ----------------
test('an expired token in storage is ignored and the landing page is shown', async () => {
  const t = await newPage();
  await t.page.goto(base);
  await t.page.evaluate(x => sessionStorage.setItem('idt', x), jwt({ expOffset: -60 }));
  await t.page.reload();
  assert.match(await t.page.locator('h1').first().innerText(), /Route it/);
  await t.close();
});

test('a 401 from the API signs the user out', async () => {
  const t = await newPage({ api: () => ({ status: 401, json: { message: 'Unauthorized' } }) });
  await signedIn(t.page, base, jwt());
  await t.page.locator('#landing').waitFor({ state: 'visible' });
  assert.equal(await t.page.evaluate(() => sessionStorage.getItem('idt')), null);
  await t.close();
});

test('sign out clears the session and returns to the landing page', async () => {
  const t = await newPage({ api: listApi([]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Sign out' }).click();
  await t.page.locator('#landing').waitFor({ state: 'visible' });
  assert.equal(await t.page.evaluate(() => sessionStorage.getItem('idt')), null);
  await t.close();
});

test('device names are rendered as text, never as HTML', async () => {
  const evil = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>';
  const t = await newPage({ api: listApi([dev({ name: evil, condition: '"><b>x</b>', address: '<i>a</i>' })]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('heading', { name: 'Your devices' }).waitFor();
  assert.equal(await t.page.evaluate(() => window.__xss), undefined);
  assert.equal(await t.page.locator('.dev b img, .dev b script').count(), 0);
  assert.ok((await t.page.locator('.dev b').first().innerText()).includes('<img src=x'));
  await t.close();
});

// ---------------- layout and accessibility ----------------
const MOBILE = { width: 360, height: 740 };
for (const [name, hash, token, role] of [
  ['landing', '', null], ['customer auth', '#customer', null], ['staff auth', '#staff', null],
  ['customer dashboard', '', jwt(), 'user'], ['collector dashboard', '', jwt({ groups: ['collectors'] }), 'collectors'],
]) {
  test(`no horizontal scrolling at 360px: ${name}`, async () => {
    const items = [dev({ name: 'A very long device name that keeps going and going to stress the layout', status: 'SCHEDULED', address: '14 Lake View Road, Some Very Long Locality Name, Pune 411001' })];
    const t = await newPage({ api: listApi(items, role) }, MOBILE);
    if (token) await signedIn(t.page, base, token); else await t.page.goto(base + hash);
    await t.page.waitForTimeout(400);
    const over = await t.page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(over <= 1, `page is ${over}px wider than the viewport`);
    await t.close();
  });
}

test('forms are labelled, the modal is a dialog and Escape closes it', async () => {
  const t = await newPage({ api: listApi([]) });
  await signedIn(t.page, base, jwt());
  await t.page.getByRole('button', { name: 'Register a device' }).click();
  const dialog = t.page.getByRole('dialog');
  assert.ok(await dialog.isVisible());
  const unlabeled = await dialog.locator('input, select, textarea').evaluateAll(els => els.filter(e => !e.closest('label')).length);
  assert.equal(unlabeled, 0);
  await t.page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  const noName = await t.page.locator('button').evaluateAll(bs => bs.filter(b => b.offsetParent && !b.textContent.trim()).length);
  assert.equal(noName, 0, 'every visible button has a text name');
  await t.close();
});

test('dark mode and light mode both render readable text (contrast spot-check)', async () => {
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ colorScheme: scheme });
    const page = await ctx.newPage();
    await mock(page, { api: listApi([dev()]) });
    await signedIn(page, base, jwt());
    await page.getByRole('heading', { name: 'Your devices' }).waitFor();
    const ratio = await page.evaluate(() => {
      const lum = c => { const [r, g, b] = c.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
      const pair = el => { const cs = getComputedStyle(el); return [cs.color, getComputedStyle(document.body).backgroundColor]; };
      const out = [];
      for (const sel of ['.page-head h1', '.page-head p', '.counter span', '.det']) {
        const [fg, bg] = pair(document.querySelector(sel));
        const a = lum(fg), b = lum(bg);
        out.push((Math.max(a, b) + .05) / (Math.min(a, b) + .05));
      }
      return out;
    });
    for (const r of ratio) assert.ok(r >= 4.5, `${scheme} contrast ${r.toFixed(2)} is below 4.5`);
    await ctx.close();
  }
});

// Regression: a helper named `history` once overwrote window.history and broke sign-out.
test('app globals never shadow browser built-ins', async () => {
  const fs = require('fs'), path = require('path');
  const html = fs.readFileSync(path.resolve(__dirname, '..', '..', 'frontend', 'index.html'), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const names = [...script.matchAll(/^(?:async\s+)?function\s+(\w+)/gm), ...script.matchAll(/^(?:const|let|var)\s+(\w+)/gm)].map(m => m[1]);
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto('about:blank');
  const clash = await page.evaluate(ns => ns.filter(n => n in window), names);
  assert.deepEqual(clash, [], 'these names collide with built-in window properties: ' + clash.join(', '));
  await ctx.close();
});
