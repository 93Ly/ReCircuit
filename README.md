# ReCircuit

ReCircuit is a serverless e-waste platform on AWS that tracks unwanted devices from pickup to repair, reuse or recycling, with role-based dashboards for customers and staff. Built for BCSE355L Cloud Architecture Design and designed around AWS Free Tier limits.

- **Customers** register devices and get a rule-based **triage** recommendation (reuse, repair or recycle), book pickups into **capacity-limited slots**, reschedule or cancel up to 24 hours before, rebook after a failed pickup, and browse the **reuse marketplace**.
- **Collectors, repair technicians and processors** each get their own task queue and email alerts for new work. Every status change is checked server-side against the role and written to an audit trail.
- Stack: S3 + CloudFront, API Gateway, nine Lambda functions (one least-privilege role each), DynamoDB (provisioned, TTL, Streams), SQS (+ DLQ), SNS, Cognito, CloudWatch. Everything stays inside AWS free allowances.

| File | Purpose |
|---|---|
| `recircuit-template.yaml` | The whole backend as one CloudFormation template (Lambda code is inline) |
| `lambdas/` | Source of the nine functions; `tools/inject_lambdas.py` copies them into the template |
| `frontend/` | Static website. Copy `config.example.js` to `config.js` and fill it in |
| `DEPLOY.md` | Deploy, update and staff-account steps |
| `DESIGN.md` | Design system ("Board") |
| `tests/` | Lambda logic tests, template lint and Chrome browser tests (`npm test`, see `tests/README.md`) |
