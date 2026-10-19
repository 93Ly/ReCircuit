import datetime
import itertools
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from harness import AWS, TEMPLATE, api_event, body, claims, device, lambda_sources, stream_event  # noqa: E402

STATUSES = ['REGISTERED', 'PICKUP_REQUESTED', 'SCHEDULED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED',
            'UNREPAIRABLE', 'RETURNED', 'PROCESSING', 'REUSED', 'RECYCLED']
ROLES = {'user': None, 'collectors': ['collectors'], 'repairers': ['repairers'], 'processors': ['processors']}

# The workflow rules, written out independently of the code under test (this is the spec).
ALLOWED = {('SCHEDULED', 'COLLECTED'): 'collectors', ('COLLECTED', 'IN_REPAIR'): 'repairers',
           ('COLLECTED', 'PROCESSING'): 'processors', ('IN_REPAIR', 'REPAIRED'): 'repairers',
           ('IN_REPAIR', 'UNREPAIRABLE'): 'repairers', ('UNREPAIRABLE', 'PROCESSING'): 'processors',
           ('REPAIRED', 'RETURNED'): 'collectors', ('PROCESSING', 'REUSED'): 'processors',
           ('PROCESSING', 'RECYCLED'): 'processors'}


class Base(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()
        self.devices_fn = self.aws.load('DevicesFn')
        self.actions_fn = self.aws.load('ActionsFn')
        self.worker_fn = self.aws.load('WorkerFn')
        self.notifier_fn = self.aws.load('NotifierFn')
        self.D = self.aws.tables['Devices']

    def seed(self, **kw):
        d = device(**kw)
        self.D.items[d['deviceId']] = d
        return d

    def call(self, fn, route, cl, payload=None, device_id=None):
        return fn.handler(api_event(route, cl, payload, device_id), None)


# ======================================================================= template
class TemplateChecks(unittest.TestCase):
    def test_inline_lambdas_fit_cloudformation_limit_and_compile(self):
        srcs = lambda_sources()
        self.assertEqual(set(srcs), {'DevicesFn', 'ActionsFn', 'WorkerFn', 'NotifierFn'})
        for name, code in srcs.items():
            self.assertLessEqual(len(code), 4096, f'{name} is {len(code)} chars; CloudFormation inline code limit is 4096')
            compile(code, name, 'exec')
            self.assertIn('def handler(', code)

    def test_security_baseline(self):
        with open(TEMPLATE, encoding='utf-8') as f:
            t = f.read()
        self.assertEqual(t.count('PublicAccessBlockConfiguration'), 2, 'both S3 buckets must block public access')
        self.assertNotRegex(t, r"Resource:\s*'\*'", 'no wildcard IAM resources')
        self.assertIn('SSEAlgorithm: AES256', t)
        self.assertIn('RedrivePolicy', t)           # dead-letter queue
        self.assertIn('StreamSpecification', t)     # DynamoDB Streams
        self.assertIn('GroupName: collectors', t)
        self.assertIn('GroupName: repairers', t)
        self.assertIn('GroupName: processors', t)
        self.assertIn('AuthorizationType: JWT', t)
        self.assertEqual(t.count('AuthorizationType: JWT'), t.count('AWS::ApiGatewayV2::Route'), 'every route needs the JWT authorizer')

    def test_every_route_has_a_handler_branch(self):
        with open(TEMPLATE, encoding='utf-8') as f:
            t = f.read()
        routes = re.findall(r'RouteKey: (.+)', t)
        code = '\n'.join(lambda_sources().values())
        for r in routes:
            self.assertIn("'" + r.strip() + "'", code, f'route {r} has no handler branch')


# ======================================================================= devices function
class DevicesFn(Base):
    def test_customer_registers_a_device(self):
        r = self.call(self.devices_fn, 'POST /devices', claims('u1'), {'name': 'Dell', 'category': 'REPAIR', 'type': 'laptop', 'condition': 'needs repair'})
        self.assertEqual(r['statusCode'], 201)
        item = list(self.D.items.values())[0]
        self.assertEqual((item['userId'], item['status'], item['category']), ('u1', 'REGISTERED', 'REPAIR'))
        self.assertEqual([h['s'] for h in item['history']], ['REGISTERED'])

    def test_validation(self):
        for bad in ({}, {'name': 'x'}, {'name': 'x', 'category': 'BURN'}, {'category': 'REUSE'}, {'name': '', 'category': 'REUSE'}):
            r = self.call(self.devices_fn, 'POST /devices', claims('u1'), bad)
            self.assertEqual(r['statusCode'], 400, bad)
        self.assertEqual(len(self.D.items), 0)

    def test_name_is_truncated(self):
        self.call(self.devices_fn, 'POST /devices', claims('u1'), {'name': 'x' * 500, 'category': 'REUSE'})
        self.assertEqual(len(list(self.D.items.values())[0]['name']), 100)

    def test_staff_cannot_register_devices_or_upload_photos(self):
        for role in ('collectors', 'repairers', 'processors'):
            cl = claims('s1', [role])
            self.assertEqual(self.call(self.devices_fn, 'POST /devices', cl, {'name': 'x', 'category': 'REUSE'})['statusCode'], 403)
            self.assertEqual(self.call(self.devices_fn, 'POST /upload-url', cl, {})['statusCode'], 403)
        self.assertEqual(len(self.D.items), 0)

    def test_upload_url_is_scoped_to_the_user(self):
        r = body(self.call(self.devices_fn, 'POST /upload-url', claims('u7'), {'contentType': 'image/png'}))
        self.assertTrue(r['photoKey'].startswith('u7/'))
        self.assertIn(r['photoKey'], r['uploadUrl'])

    def test_customers_only_see_their_own_devices(self):
        self.seed(deviceId='a', userId='u1')
        self.seed(deviceId='b', userId='u2')
        got = body(self.call(self.devices_fn, 'GET /devices', claims('u1')))
        self.assertEqual([i['deviceId'] for i in got['items']], ['a'])
        self.assertEqual(got['role'], 'user')

    def test_photo_gets_a_signed_url(self):
        self.seed(photoKey='u1/abc')
        item = body(self.call(self.devices_fn, 'GET /devices', claims('u1')))['items'][0]
        self.assertIn('u1/abc', item['photoUrl'])

    def test_each_staff_role_sees_only_its_queue(self):
        seeds = []
        for status, cat in itertools.product(STATUSES, ['REPAIR', 'REUSE', 'RECYCLE']):
            seeds.append(self.seed(deviceId=f'{status}-{cat}', status=status, category=cat, address='1 Main St'))
        expected = {
            'collectors': {s for s in STATUSES if s in ('SCHEDULED', 'COLLECTED', 'REPAIRED', 'RETURNED')},
            'repairers': {s for s in STATUSES if s in ('IN_REPAIR', 'REPAIRED', 'UNREPAIRABLE')},
            'processors': {s for s in STATUSES if s in ('UNREPAIRABLE', 'PROCESSING', 'REUSED', 'RECYCLED')},
        }
        for role, statuses in expected.items():
            got = body(self.call(self.devices_fn, 'GET /devices', claims('s', [role])))
            ids = {i['deviceId'] for i in got['items']}
            want = {f'{s}-{c}' for s in statuses for c in ('REPAIR', 'REUSE', 'RECYCLE')}
            if role == 'repairers':
                want.add('COLLECTED-REPAIR')          # incoming repairs
            if role == 'processors':
                want |= {'COLLECTED-REUSE', 'COLLECTED-RECYCLE'}  # incoming reuse / recycle
            self.assertEqual(ids, want, role)
            self.assertEqual(got['role'], role)

    def test_only_collectors_see_addresses_and_owner_ids(self):
        self.seed(status='SCHEDULED', address='221B Baker St')
        self.seed(deviceId='r', status='IN_REPAIR', address='221B Baker St')
        c = body(self.call(self.devices_fn, 'GET /devices', claims('s', ['collectors'])))['items']
        self.assertEqual(c[0]['address'], '221B Baker St')
        self.assertIn('userId', c[0])
        for role in ('repairers', 'processors'):
            self.seed(deviceId='p', status='PROCESSING', category='REUSE', address='221B Baker St')
            for i in body(self.call(self.devices_fn, 'GET /devices', claims('s', [role])))['items']:
                self.assertNotIn('address', i, role)
                self.assertNotIn('userId', i, role)

    def test_unknown_route(self):
        self.assertEqual(self.call(self.devices_fn, 'DELETE /devices', claims('u1'))['statusCode'], 404)


# ======================================================================= actions function
class StatusTransitions(Base):
    def test_full_transition_matrix(self):
        """Every (from, to) pair x every role x every category: only the spec'd role may move a device, and nothing changes otherwise."""
        checked = 0
        for frm, to, (role, groups), cat in itertools.product(STATUSES, STATUSES, ROLES.items(), ['REPAIR', 'REUSE', 'RECYCLE']):
            d = self.seed(status=frm, category=cat, history=[{'s': frm, 't': 1}])
            r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims('actor', groups), {'status': to}, 'd1')
            owner = ALLOWED.get((frm, to))
            if owner is None:
                want = 409
            elif owner != role:
                want = 403
            elif (frm, to) == ('COLLECTED', 'IN_REPAIR') and cat != 'REPAIR':
                want = 409
            elif (frm, to) == ('COLLECTED', 'PROCESSING') and cat == 'REPAIR':
                want = 409
            else:
                want = 200
            self.assertEqual(r['statusCode'], want, f'{role} {frm}->{to} ({cat})')
            now = self.D.items['d1']
            if want == 200:
                self.assertEqual(now['status'], to)
                self.assertEqual([h['s'] for h in now['history']], [frm, to])
            else:
                self.assertEqual(now['status'], frm, 'a rejected change must not modify the device')
                self.assertEqual(len(now['history']), 1)
            checked += 1
        self.assertEqual(checked, 11 * 11 * 4 * 3)

    def test_customer_cannot_mark_their_own_device_collected(self):
        self.seed(status='SCHEDULED')
        r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims('u1'), {'status': 'COLLECTED'}, 'd1')
        self.assertEqual(r['statusCode'], 403)

    def test_garbage_status_values(self):
        self.seed(status='SCHEDULED')
        for bad in (None, '', 'collected', 'DROP TABLE', 123, ['COLLECTED']):
            r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims('c', ['collectors']), {'status': bad}, 'd1')
            self.assertIn(r['statusCode'], (400, 409), bad)

    def test_unknown_device(self):
        r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims('c', ['collectors']), {'status': 'COLLECTED'}, 'nope')
        self.assertEqual(r['statusCode'], 404)

    def test_role_claim_formats_from_api_gateway(self):
        self.seed(status='SCHEDULED')
        for fmt in ('[collectors]', 'collectors', '[repairers collectors]'):
            self.D.items['d1']['status'] = 'SCHEDULED'
            cl = {'sub': 'c', 'email': 'c@x.com', 'cognito:groups': fmt}
            self.assertEqual(self.call(self.actions_fn, 'PATCH /devices/{id}/status', cl, {'status': 'COLLECTED'}, 'd1')['statusCode'], 200, fmt)

    def test_a_group_name_inside_another_word_does_not_grant_a_role(self):
        self.seed(status='SCHEDULED')
        cl = {'sub': 'c', 'email': 'c@x.com', 'cognito:groups': '[notcollectors]'}
        self.assertEqual(self.call(self.actions_fn, 'PATCH /devices/{id}/status', cl, {'status': 'COLLECTED'}, 'd1')['statusCode'], 403)


class Pickup(Base):
    def test_owner_requests_pickup(self):
        self.seed()
        r = self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('u1'), {'address': '  12 High St  '}, 'd1')
        self.assertEqual(r['statusCode'], 202)
        d = self.D.items['d1']
        self.assertEqual((d['status'], d['address']), ('PICKUP_REQUESTED', '12 High St'))
        self.assertEqual([h['s'] for h in d['history']], ['REGISTERED', 'PICKUP_REQUESTED'])
        req = self.aws.tables['Requests'].items['d1']
        self.assertEqual((req['userId'], req['status']), ('u1', 'PENDING'))
        sent = [c for c in self.aws.sqs.calls if c[0] == 'send_message']
        self.assertEqual(len(sent), 1)
        self.assertEqual(json.loads(sent[0][1]['MessageBody']), {'requestId': 'd1', 'deviceId': 'd1'})

    def test_someone_else_cannot_request_it(self):
        self.seed()
        self.assertEqual(self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('intruder'), {'address': 'x'}, 'd1')['statusCode'], 403)
        self.assertEqual(self.D.items['d1']['status'], 'REGISTERED')

    def test_staff_cannot_request_pickups_even_as_owner(self):
        self.seed(userId='s1')
        self.assertEqual(self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('s1', ['collectors']), {'address': 'x'}, 'd1')['statusCode'], 403)

    def test_only_once(self):
        self.seed(status='SCHEDULED')
        self.assertEqual(self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('u1'), {'address': 'x'}, 'd1')['statusCode'], 409)
        self.assertEqual(self.aws.sqs.calls, [])

    def test_address_required_and_capped(self):
        self.seed()
        for bad in ({}, {'address': ''}, {'address': '   '}):
            self.assertEqual(self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('u1'), bad, 'd1')['statusCode'], 400, bad)
        self.assertEqual(self.aws.sqs.calls, [])
        self.call(self.actions_fn, 'POST /devices/{id}/pickup', claims('u1'), {'address': 'a' * 999}, 'd1')
        self.assertEqual(len(self.D.items['d1']['address']), 200)


class Subscribe(Base):
    def test_subscribes_with_a_per_user_filter(self):
        r = self.call(self.actions_fn, 'POST /subscribe', claims('u9', email='nine@x.com'))
        self.assertEqual(r['statusCode'], 200)
        kw = [c for c in self.aws.sns.calls if c[0] == 'subscribe'][0][1]
        self.assertEqual((kw['Protocol'], kw['Endpoint']), ('email', 'nine@x.com'))
        self.assertEqual(json.loads(kw['Attributes']['FilterPolicy']), {'userId': ['u9']})

    def test_existing_subscription_is_not_a_server_error(self):
        self.aws.sns.fail_subscribe = True
        r = self.call(self.actions_fn, 'POST /subscribe', claims('u9'))
        self.assertEqual(r['statusCode'], 200)
        self.assertIn('already subscribed', body(r)['message'])


# ======================================================================= worker + notifier
class Worker(Base):
    def test_schedules_pickups_two_days_out(self):
        self.seed(deviceId='a', status='PICKUP_REQUESTED', history=[{'s': 'REGISTERED', 't': 1}])
        self.seed(deviceId='b', status='PICKUP_REQUESTED', history=[{'s': 'REGISTERED', 't': 1}])
        for i in 'ab':
            self.aws.tables['Requests'].items[i] = {'requestId': i, 'status': 'PENDING'}
        ev = {'Records': [{'body': json.dumps({'requestId': i, 'deviceId': i})} for i in 'ab']}
        self.worker_fn.handler(ev, None)
        day = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        for i in 'ab':
            d = self.D.items[i]
            self.assertEqual((d['status'], d['slot']), ('SCHEDULED', day + ' 10:00-13:00'))
            self.assertEqual([h['s'] for h in d['history']], ['REGISTERED', 'SCHEDULED'])
            self.assertEqual(self.aws.tables['Requests'].items[i]['status'], 'SCHEDULED')

    def test_bad_message_raises_so_sqs_retries_then_dead_letters(self):
        with self.assertRaises(Exception):
            self.worker_fn.handler({'Records': [{'body': 'not json'}]}, None)


class Notifier(Base):
    def publishes(self):
        return [c[1] for c in self.aws.sns.calls if c[0] == 'publish']

    def test_one_email_per_status_change_addressed_to_the_owner(self):
        old, new = device(status='SCHEDULED'), device(status='COLLECTED', slot='2026-10-21 10:00-13:00')
        self.notifier_fn.handler(stream_event([(old, new)]), None)
        p = self.publishes()
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['MessageAttributes']['userId']['StringValue'], 'u1')
        self.assertIn("'Laptop'", p[0]['Message'])
        self.assertIn('Collected', p[0]['Message'])

    def test_scheduled_message_includes_the_slot(self):
        self.notifier_fn.handler(stream_event([(device(status='PICKUP_REQUESTED'), device(status='SCHEDULED', slot='2026-10-21 10:00-13:00'))]), None)
        self.assertIn('2026-10-21 10:00-13:00', self.publishes()[0]['Message'])

    def test_no_email_for_inserts_or_unchanged_status(self):
        self.notifier_fn.handler(stream_event([(None, device())]), None)
        self.notifier_fn.handler(stream_event([(device(status='SCHEDULED'), device(status='SCHEDULED', slot='x'))]), None)
        self.assertEqual(self.publishes(), [])

    def test_every_status_has_friendly_wording(self):
        for s in STATUSES[1:]:
            self.aws.sns.calls.clear()
            self.notifier_fn.handler(stream_event([(device(status='REGISTERED'), device(status=s))]), None)
            msg = self.publishes()[0]['Message']
            self.assertTrue(msg.rstrip().endswith('.') and len(msg) > 40, s)


# ======================================================================= the whole journey
class Lifecycle(Base):
    def journey(self, category, steps):
        u = claims('cust', email='cust@x.com')
        did = body(self.call(self.devices_fn, 'POST /devices', u, {'name': 'Dell', 'category': category}))['deviceId']
        self.assertEqual(self.call(self.actions_fn, 'POST /devices/{id}/pickup', u, {'address': '1 Main St'}, did)['statusCode'], 202)
        queued = [c[1] for c in self.aws.sqs.calls if c[0] == 'send_message']
        self.worker_fn.handler({'Records': [{'body': q['MessageBody']} for q in queued]}, None)
        for role, to in steps:
            r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims(role + '1', [role]), {'status': to}, did)
            self.assertEqual(r['statusCode'], 200, f'{role} -> {to}: {r["body"]}')
        return self.D.items[did], did

    def check_emails(self, expected_statuses):
        # replay what DynamoDB Streams would deliver, then check the owner got one email per change
        self.notifier_fn.handler(stream_event(self.aws.stream), None)
        pub = [c[1] for c in self.aws.sns.calls if c[0] == 'publish']
        self.assertTrue(all(p['MessageAttributes']['userId']['StringValue'] == 'cust' for p in pub))
        for s in expected_statuses:
            self.assertTrue(any(s.replace('_', ' ').title() in p['Message'] for p in pub), 'no email for ' + s)

    def test_repair_and_return(self):
        steps = [('collectors', 'COLLECTED'), ('repairers', 'IN_REPAIR'), ('repairers', 'REPAIRED'), ('collectors', 'RETURNED')]
        d, _ = self.journey('REPAIR', steps)
        self.assertEqual(d['status'], 'RETURNED')
        self.assertEqual([h['s'] for h in d['history']], ['REGISTERED', 'PICKUP_REQUESTED', 'SCHEDULED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED', 'RETURNED'])
        self.check_emails(['SCHEDULED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED', 'RETURNED'])

    def test_recycle(self):
        d, _ = self.journey('RECYCLE', [('collectors', 'COLLECTED'), ('processors', 'PROCESSING'), ('processors', 'RECYCLED')])
        self.assertEqual(d['status'], 'RECYCLED')
        self.check_emails(['COLLECTED', 'PROCESSING', 'RECYCLED'])

    def test_reuse(self):
        d, _ = self.journey('REUSE', [('collectors', 'COLLECTED'), ('processors', 'PROCESSING'), ('processors', 'REUSED')])
        self.assertEqual(d['status'], 'REUSED')

    def test_unrepairable_device_falls_through_to_processors(self):
        steps = [('collectors', 'COLLECTED'), ('repairers', 'IN_REPAIR'), ('repairers', 'UNREPAIRABLE'), ('processors', 'PROCESSING'), ('processors', 'RECYCLED')]
        d, did = self.journey('REPAIR', steps)
        self.assertEqual(d['status'], 'RECYCLED')
        # and a repair-category device cannot skip the repair bench
        d2, did2 = self.journey('REPAIR', [('collectors', 'COLLECTED')])
        r = self.call(self.actions_fn, 'PATCH /devices/{id}/status', claims('p', ['processors']), {'status': 'PROCESSING'}, did2)
        self.assertEqual(r['statusCode'], 409)


if __name__ == '__main__':
    unittest.main()
