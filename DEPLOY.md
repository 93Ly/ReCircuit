# ReCircuit: deploy and update guide (all Free Tier services)

Region: **Asia Pacific (Sydney) ap-southeast-2**.

## First deploy
1. **CloudFormation -> Create stack -> With new resources -> Upload** `recircuit-template.yaml`.
2. Stack name `recircuit`, AdminEmail = your email. Submit and wait for `CREATE_COMPLETE`.
3. Confirm the SNS subscription email.
4. Put the Outputs (`ApiUrl`, `ClientId`, `Region`) into `frontend/config.js`.
5. Upload `frontend/index.html` and `frontend/config.js` to the `SiteBucketName` bucket.

## Updating after you change the template or the website
- Template changed: **CloudFormation -> recircuit -> Update stack -> Make a direct update -> Replace existing template**, upload, Next, Next, Submit.
- Website changed: re-upload `index.html` / `config.js` to the site bucket and hard-refresh (Ctrl+Shift+R).

## Roles and who can do what
| Role (Cognito group) | Portal | Can do |
|---|---|---|
| *(no group)* Customer | Customer portal (self sign-up) | Register devices, request pickup, track status |
| `collectors` | Staff portal | SCHEDULED -> COLLECTED, REPAIRED -> RETURNED (deliver back to customer) |
| `repairers` | Staff portal | COLLECTED -> IN_REPAIR -> REPAIRED or UNREPAIRABLE (only devices marked for repair) |
| `processors` | Staff portal | COLLECTED / UNREPAIRABLE -> PROCESSING -> REUSED or RECYCLED |

The workflow rules are enforced in the Lambda function, not just in the page.

Flow: Registered -> Pickup requested -> Scheduled (automatic, SQS worker) -> Collected -> (Repair path: In repair -> Repaired -> Returned) or (Processing -> Reused / Recycled).

## Creating staff accounts (administrator, in the AWS console)
Staff cannot sign up themselves; you create them.
1. **Cognito -> User pools -> recircuit-users -> Users -> Create user**.
2. Email = the staff member's email, tick **Mark email address as verified**, choose **Set a password** (temporary), Create.
3. Open the user -> **Add user to group** -> pick `collectors`, `repairers` or `processors` (one group only).
4. The staff member signs in on the **Staff portal**; they are asked to choose a new password on first login.

Tip for testing alone: Gmail ignores `+tags`, so `you+collector@gmail.com`, `you+repair@gmail.com`, `you+recycle@gmail.com` are different Cognito users that all land in your inbox.

Customers who sign up on the Customer portal have no group. Do **not** put your customer test account in a staff group; a staff account cannot sign in to the customer portal.

## Clean up when finished
Empty both S3 buckets, then delete the `recircuit` stack.
