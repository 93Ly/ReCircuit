# ReCircuit tests

Run everything (from this folder): `npm test`. No AWS account, network or cost involved.

| Suite | Files | What it covers |
|---|---|---|
| Template and cost guards | `backend/test_template.py` | Inline code under 4096 characters, template matches `../lambdas/`, every environment variable the code reads is defined, **each function has its own role with exactly the permitted actions**, no wildcards, only reviewed free-tier resource types, DynamoDB provisioned within the always-free 25/25 units with TTL, simple (not metric-math) alarms, photo retention rule |
| Workflow and audit | `backend/test_workflow.py` | The full role x status x category matrix (2,352 combinations), audit entries (who, what, when), retention dates, stale-write races, and complete journeys: repair, recycle, reuse, unrepairable, failed pickup then rebook, marketplace |
| Features | `backend/test_features.py` | Smart triage rules, registration, listing and visibility, slot capacity and the last-seat race, duplicate queue messages, reschedule and cancel with the 24-hour cutoff, failed pickups, marketplace (privacy, filters, reserve race, double listing), staff and customer notifications, subscriptions |
| Security and abuse | `backend/test_security.py` | One customer cannot see or act on another customer's devices, photos or address; photo keys cannot be forged; the marketplace leaks nothing; hostile and malformed requests (over 1,000 combinations) never crash a function; repeated requests do not double-apply |
| Browser | `e2e/app.test.js`, `e2e/features.test.js` | Chrome against `../frontend` with Cognito and the API mocked: sign-in flows, portals, registration with live triage, pickup, reschedule, cancel, failed pickup, marketplace, every staff button and the exact status it sends, errors, sessions, XSS, layout at 360 px, accessibility, contrast |
| Lint | `.venv` + `cfn-lint` | CloudFormation template validation |

The Lambda tests run the code that is inside `../recircuit-template.yaml` against in-memory fakes of DynamoDB (including conditional writes), S3, SQS and SNS.

## Setup (once)
```
cd tests
npm install                      # playwright-core (uses the Chrome already installed; no browser download)
py -m venv .venv
.venv\Scripts\python -m pip install cfn-lint pyyaml python-docx pymupdf pillow
```

## Individual commands
- `npm run test:backend` Python tests only (use the venv: `.venv\Scripts\python -W ignore -m unittest discover -s backend`)
- `npm run test:e2e` browser tests only
- `npm run screenshots` writes desktop/mobile, light/dark screenshots of every view to `tests/screenshots/` (look at them after any visual change; see `../DESIGN.md`)

## When you change things
- Changed function code -> edit `../lambdas/*.py`, run `..\tools\inject_lambdas.py`, then the tests. `test_template_matches_the_lambdas_folder` fails if they drift apart.
- Changed workflow rules -> update `ALLOWED` in `backend/test_workflow.py` first (it is the written spec), then make the code match.
- Changed IAM permissions -> update `ALLOWED` in `backend/test_template.py` first; a function must never get more than its spec.
- Added a DynamoDB table or capacity -> `test_dynamodb_stays_inside_the_always_free_allowance` keeps the total under 25/25 units.
- Changed the UI -> run `npm test` and `npm run screenshots`, and keep `../DESIGN.md` in sync.
- Found a bug -> add a failing test for it first (this is how the `history` shadowing bug, the list-as-status 500 and the photo-key leak are now covered).
