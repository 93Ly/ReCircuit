# ReCircuit: deploy and update guide (all Free Tier services)

Region: **Asia Pacific (Sydney) ap-southeast-2**.

## First deploy
1. **CloudFormation -> Create stack -> With new resources -> Upload** `recircuit-template.yaml`.
2. Stack name `recircuit`, AdminEmail = your email. Leave `SlotCapacity` (3) and `CancelCutoffHours` (24) as they are. Submit and wait for `CREATE_COMPLETE`.
3. Confirm the SNS subscription email.
4. Copy the Outputs (`ApiUrl`, `ClientId`, `Region`) into `frontend/config.js` (start from `config.example.js`).
5. Upload `frontend/index.html` and `frontend/config.js` to the `SiteBucketName` bucket.

## Updating an existing deployment
1. **CloudFormation -> recircuit -> Update stack -> Make a direct update -> Replace existing template**, upload the new `recircuit-template.yaml`, keep the parameters, Next, Next, Submit. Wait for `UPDATE_COMPLETE`.
2. What this update changes: DynamoDB moves from on-demand to **provisioned capacity inside the always-free allowance**, a `Slots` table is added, TTL retention is switched on, the shared Lambda role is replaced by **nine functions with one least-privilege role each**, and new API routes appear. The API address, Cognito client and website address stay the same, so `config.js` does not change.
3. If CloudFormation refuses the billing-mode change (DynamoDB allows one switch per 24 hours), wait a day and retry, or delete the stack and create it again (test data is lost).
4. Upload the new `frontend/index.html` to the site bucket and hard-refresh (Ctrl+Shift+R).

Editing function code: change the file in `lambdas/`, run `tests\.venv\Scripts\python tools\inject_lambdas.py` (it copies the code into the template and refuses files over CloudFormation's 4096-character inline limit), run the tests, then update the stack.

## Roles and what each can do
| Role (Cognito group) | Portal | Can do |
|---|---|---|
| *(no group)* Customer | Customer tab (self sign-up) | Register devices (with triage advice), request a pickup, **reschedule or cancel it up to 24 hours before**, request another slot after a failed pickup, browse and reserve from the **reuse marketplace**, follow the journey and history |
| `collectors` | Staff tab | Mark collected, **record a failed collection with a reason**, deliver repaired devices back |
| `repairers` | Staff tab | Start repair, mark repaired or unrepairable |
| `processors` | Staff tab | Start processing, mark reused or recycled, **list a device on the marketplace**, delist it, **confirm a handover**, release a reservation |

Every step is checked by the server. Each change is written to the device history with who (by role), what and when.

Flow: Registered -> Pickup requested -> Scheduled (slot booked automatically, at most `SlotCapacity` pickups per slot) -> Collected (or **Pickup failed**, after which the customer can book again, three tries) -> repair path (In repair -> Repaired -> Returned) or processing path (Processing -> Reused, Recycled, or **Listed -> Reserved -> Reused**). A device the technician cannot fix goes to the processors.

## Creating staff accounts (administrator, in the AWS console)
Staff cannot sign up themselves.
1. **Cognito -> User pools -> recircuit-users -> Users -> Create user**.
2. Email = the staff member's email, tick **Mark email address as verified**, choose **Set a password** (temporary), Create.
3. Open the user -> **Add user to group** -> `collectors`, `repairers` or `processors` (one group only).
4. They sign in on the **Staff** tab and choose a new password on first login. On their dashboard **Email me new tasks** subscribes them to alerts for their role only.

Tip for testing alone: Gmail ignores `+tags`, so `you+collector@gmail.com`, `you+repair@gmail.com` and `you+recycle@gmail.com` are different Cognito users that all land in your inbox.

## What it costs
The design is built to stay at **zero cost** on the free allowances published by AWS:
- DynamoDB: **provisioned** capacity totalling 11 read and 11 write units, under the always-free 25 and 25 (a test fails if this ever goes over). TTL deletes expired data for free.
- Lambda (1M requests and 400,000 GB-seconds a month), SQS (1M requests), SNS (1M publishes, **1,000 email deliveries a month**), CloudFront (1 TB, 10M requests), Cognito (10,000 monthly users) and CloudWatch (10 standard alarms, we use 2) are all always-free allowances far above project-scale use.
- API Gateway's 1M free calls last 12 months for new accounts; beyond that, or on the credit-based plan, calls are covered by credits and cost fractions of a cent at this volume.
- Not used because they cost money: NAT gateway, load balancer, RDS, WAF, AWS Config, customer-managed KMS keys. Step Functions was evaluated and left out (see the report).
- Keep the zero-spend budget alert on.

## Clean up when finished
Empty the website, photo and `cf-templates-...` S3 buckets, delete the `recircuit` stack, then delete the `/aws/lambda/recircuit-...` log groups.
