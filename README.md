# ReCircuit

ReCircuit is a serverless e-waste platform on AWS that tracks unwanted devices from pickup to repair, reuse or recycling, with role-based dashboards for customers and staff. Built for BCSE355L Cloud Architecture Design and designed around AWS Free Tier limits.

- **Customers** register devices, book pickups and track them.
- **Collectors, repair technicians and processors** each get their own staff queue; every status change is checked server-side against the role.
- Stack: S3 + CloudFront, API Gateway, Lambda, DynamoDB (+ Streams), SQS (+ DLQ), SNS, Cognito, CloudWatch.

| File | Purpose |
|---|---|
| `recircuit-template.yaml` | The whole backend as one CloudFormation template (Lambda code is inline) |
| `frontend/` | Static website. Copy `config.example.js` to `config.js` and fill it in |
| `DEPLOY.md` | Deploy, update and staff-account steps |
| `DESIGN.md` | Design system ("Board") |
| `tests/` | Lambda logic tests, template lint and Chrome browser tests (`npm test`, see `tests/README.md`) |
