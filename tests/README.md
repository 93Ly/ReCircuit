# ReCircuit tests

Run everything (from this folder): `npm test`. No AWS account, network or cost involved.

| Suite | File | What it covers |
|---|---|---|
| Lambda logic | `backend/test_lambdas.py` | The real Lambda code is pulled out of `../recircuit-template.yaml` and run against in-memory fakes of DynamoDB / S3 / SQS / SNS. Covers: the full role x status x category transition matrix (1,452 combinations), who sees which devices, address privacy, pickup rules, SNS subscription, the SQS worker, the stream notifier, and the whole repair / reuse / recycle / unrepairable journeys end to end. Also template checks (4096-char inline limit, S3 public access blocked, every route JWT-protected, no wildcard IAM). |
| Browser | `e2e/app.test.js` | Playwright driving your installed Chrome against `../frontend`, with Cognito and the API mocked. Covers: landing page, separate customer/staff portals, sign-up/verify/sign-in, wrong password, unconfirmed account, forced new password, wrong-portal rejection, device registration + photo upload, pickup form, per-role staff buttons and the exact status each sends, API errors, expired token / 401 / sign-out, XSS escaping, tracker state, no horizontal scroll at 360px, labels/dialog/Escape, light+dark contrast, and a guard against globals shadowing browser built-ins. |
| Lint | `.venv` + `cfn-lint` | CloudFormation template validation (optional; created by the setup below). |

## Setup (once)
```
cd tests
npm install                      # playwright-core (uses the Chrome already installed; no browser download)
py -m venv .venv
.venv\Scripts\python -m pip install cfn-lint
```

## Individual commands
- `npm run test:backend` Python tests only
- `npm run test:e2e` browser tests only
- `npm run screenshots` writes desktop/mobile, light/dark screenshots of every view to `tests/screenshots/` (look at them after any visual change; see `../DESIGN.md`)

## When you change things
- Changed the Lambda code or workflow rules in the template -> update `ALLOWED` in `test_lambdas.py` first (it is the written spec), then make the code match.
- Added a route -> add its handler branch; `test_every_route_has_a_handler_branch` will fail until you do.
- Changed the UI -> run `npm test` and `npm run screenshots`, and keep `DESIGN.md` in sync.
- Found a bug in production -> add a failing test for it first (this is how the `history` shadowing bug and the list-as-status 500 are now covered).
