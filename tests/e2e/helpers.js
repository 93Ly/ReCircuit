// Shared helpers for the browser tests: static server, fake JWTs, mocked Cognito + API.
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const FRONTEND = path.resolve(__dirname, '..', '..', 'frontend');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };

function serve() {
  return new Promise(resolve => {
    const server = http.createServer((req, res) => {
      let f = path.join(FRONTEND, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
      // config.js is git-ignored; on a fresh clone fall back to the committed example
      if (!fs.existsSync(f) && path.basename(f) === 'config.js') f = path.join(FRONTEND, 'config.example.js');
      if (!f.startsWith(FRONTEND) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' });
      fs.createReadStream(f).pipe(res);
    }).listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/` }));
  });
}

const b64 = o => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt({ email = 'person@example.com', groups, expOffset = 3600 } = {}) {
  const payload = { sub: 'user-1', email, exp: Math.floor(Date.now() / 1000) + expOffset };
  if (groups) payload['cognito:groups'] = groups;
  return `${b64({ alg: 'none' })}.${b64(payload)}.sig`;
}

function config() {
  // read the real config.js so the tests follow whatever the project is configured with
  const real = path.join(FRONTEND, 'config.js');
  const src = fs.readFileSync(fs.existsSync(real) ? real : path.join(FRONTEND, 'config.example.js'), 'utf8');
  const get = k => (src.match(new RegExp(k + ":\\s*'([^']+)'")) || [])[1];
  return { region: get('region'), apiUrl: get('apiUrl'), clientId: get('clientId') };
}

async function launch() {
  return chromium.launch({ channel: 'chrome', headless: true });
}

/**
 * Mock Cognito and the REST API.
 * opts.cognito(target, body) -> { status?, json } ; opts.api(method, path, body) -> { status?, json }
 * Every request is recorded in the returned `calls` array.
 */
async function mock(page, opts = {}) {
  const cfg = config();
  const calls = [];
  await page.route(`https://cognito-idp.${cfg.region}.amazonaws.com/**`, async route => {
    const req = route.request();
    const target = (req.headers()['x-amz-target'] || '').split('.').pop();
    const body = JSON.parse(req.postData() || '{}');
    calls.push({ kind: 'cognito', target, body });
    const r = (opts.cognito && opts.cognito(target, body)) || { json: {} };
    await route.fulfill({ status: r.status || 200, contentType: 'application/x-amz-json-1.1', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(r.json) });
  });
  await page.route(`${cfg.apiUrl}/**`, async route => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' } });
    const p = new URL(req.url()).pathname;
    const body = req.postData() ? JSON.parse(req.postData()) : undefined;
    calls.push({ kind: 'api', method: req.method(), path: p, body, auth: req.headers()['authorization'] });
    const r = (opts.api && opts.api(req.method(), p, body)) || { json: {} };
    await route.fulfill({ status: r.status || 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(r.json) });
  });
  await page.route('https://uploads.example/**', route => route.fulfill({ status: 200, headers: { 'access-control-allow-origin': '*' }, body: '' }));
  return calls;
}

async function signedIn(page, base, token) {
  await page.goto(base);
  await page.evaluate(t => sessionStorage.setItem('idt', t), token);
  await page.reload();
}

const dev = (o = {}) => ({
  deviceId: 'abcdef123456', userId: 'user-1', name: 'Dell Laptop', type: 'laptop', condition: 'working', age: 3,
  category: 'REUSE', assessment: { recommended: 'REUSE', reason: 'Working and at most 8 years old: suitable for refurbishment and reuse' }, status: 'REGISTERED', createdAt: 1760000000, history: [{ s: 'REGISTERED', t: 1760000000 }], ...o
});

module.exports = { serve, launch, mock, jwt, config, signedIn, dev };
