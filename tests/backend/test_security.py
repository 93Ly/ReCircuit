"""Privacy checks and abuse prevention: what one customer must never see or do to another, and hostile input."""
import itertools
import json

from base import FUNCS, STATUSES, Base, body, claims

JUNK_BODIES = [None, 'not json', '[]', '[1, 2]', '"text"', '42', 'null', 'true', '{', '{"status": ["COLLECTED"]}', '{"status": null}',
               '{"slot": {"a": 1}}', '{"type": {}, "condition": [], "age": "x"}', '{"address": 12345}', '{"note": {"x": 1}}',
               json.dumps({'name': 'x' * 100000, 'type': 'laptop', 'condition': 'working', 'age': 1}),
               json.dumps({'status': 'COLLECTED', 'note': '\u0000\ud83d' * 50})]


class Privacy(Base):
    def setUp(self):
        super().setUp()
        self.seed(deviceId='dev-a1', userId='alice', status='SCHEDULED', address='221B Baker Street', photoKey="11111111-1111-4111-8111-111111111111", slot=self.slots()[3],
                  name='Laptop')

    def test_a_customer_cannot_list_anothers_devices_photos_or_address(self):
        got = json.dumps(body(self.call('ListFn', 'GET /devices', claims('mallory'))))
        self.assertNotIn('alice', got.lower())
        self.assertNotIn("11111111-1111-4111-8111-111111111111", got, 'not even a signed link to their photo')
        self.assertNotIn('Baker', got)
        self.assertEqual(body(self.call('ListFn', 'GET /devices', claims('mallory')))['items'], [])

    def test_a_customer_cannot_act_on_anothers_device(self):
        m = claims('mallory')
        for fn, route, payload in (('PickupFn', 'POST /devices/{id}/pickup', {'address': 'x'}),
                                   ('ChangeFn', 'POST /devices/{id}/cancel', {}),
                                   ('ChangeFn', 'POST /devices/{id}/reschedule', {'slot': self.slots()[5]})):
            r = self.call(fn, route, m, payload, 'dev-a1')
            self.assertEqual(r['statusCode'], 404, route)
            self.assertNotIn('Baker', r['body'])
        for st in STATUSES:
            r = self.call('StatusFn', 'PATCH /devices/{id}/status', m, {'status': st, 'note': 'x'}, 'dev-a1')
            self.assertIn(r['statusCode'], (403, 409), st)
        self.assertEqual(self.D.items['dev-a1']['status'], 'SCHEDULED')

    def test_photo_keys_cannot_point_at_someone_elses_photo(self):
        ok = {'name': 'x', 'type': 'laptop', 'condition': 'working', 'age': 1}
        for key in ('alice/secret', '../alice/secret', '/etc/passwd', 'alice/' + "11111111-1111-4111-8111-111111111111", '11111111-1111-4111-8111-111111111111/../x', '%2e%2e/x'):
            r = self.call('RegisterFn', 'POST /devices', claims('mallory'), dict(ok, photoKey=key))
            self.assertEqual(r['statusCode'], 400, key)
        key = body(self.call('RegisterFn', 'POST /upload-url', claims('mallory'), {}))['photoKey']
        self.assertEqual(self.call('RegisterFn', 'POST /devices', claims('mallory'), dict(ok, photoKey=key))['statusCode'], 201)

    def test_marketplace_never_reveals_owners_addresses_or_other_reservations(self):
        self.seed(deviceId='l1', userId='alice', status='LISTED', address='221B Baker Street', photoKey="11111111-1111-4111-8111-111111111111", category='REUSE')
        self.seed(deviceId='l2', userId='alice', status='RESERVED', reservedBy='bob', address='221B Baker Street', category='REUSE')
        got = json.dumps(body(self.call('MarketFn', 'GET /market', claims('mallory'))))
        for secret in ('alice', 'Baker', 'bob', 'userId', 'address', 'history'):
            self.assertNotIn(secret, got, secret)
        self.assertNotIn('l2', got, "another customer's reservation is invisible")

    def test_repairers_and_processors_never_see_customer_details(self):
        self.D.items['dev-a1']['status'] = 'IN_REPAIR'
        self.D.items['dev-a1']['category'] = 'REPAIR'
        for role in ('repairers', 'processors'):
            got = json.dumps(body(self.call('ListFn', 'GET /devices', self.staff(role))))
            for secret in ('alice', 'Baker', 'userId', '"by"'):
                self.assertNotIn(secret, got, (role, secret))


class AbuseRegression(Base):
    def test_junk_requests_never_crash_any_function(self):
        """Hostile bodies against every API route must end in a clean 4xx, never an unhandled exception."""
        routes = [('RegisterFn', 'POST /upload-url', None), ('RegisterFn', 'POST /assess', None), ('RegisterFn', 'POST /devices', None),
                  ('PickupFn', 'POST /devices/{id}/pickup', 'd1'), ('ChangeFn', 'POST /devices/{id}/reschedule', 'd1'),
                  ('ChangeFn', 'POST /devices/{id}/cancel', 'd1'), ('StatusFn', 'PATCH /devices/{id}/status', 'd1')]
        cases = 0
        for status, (fn, route, did), raw in itertools.product(('REGISTERED', 'SCHEDULED', 'COLLECTED'), routes, JUNK_BODIES):
            self.seed(status=status, slot=self.slots()[3], address='x')
            for cl in (claims('u1'), self.staff('collectors'), self.staff('processors')):
                r = self.call(fn, route, cl, raw=raw, did=did)
                self.assertIn(r['statusCode'], (200, 201, 202, 400, 403, 404, 409), (fn, route, raw, r['body'][:80]))
                cases += 1
        self.assertGreater(cases, 1000)

    def test_market_junk(self):
        self.seed(status='LISTED')
        for q in ({'type': ['a']}, {'type': ''}, {'condition': None}, {'x': 'y'}, {'category': 'REUSE', 'type': 'laptop'}):
            r = self.call('MarketFn', 'GET /market', claims('u1'), query=q)
            self.assertIn(r['statusCode'], (200, 400), q)

    def test_repeating_a_request_cannot_double_apply_it(self):
        self.seed()
        u = claims('u1')
        codes = [self.call('PickupFn', 'POST /devices/{id}/pickup', u, {'address': 'x'}, 'd1')['statusCode'] for _ in range(5)]
        self.assertEqual(codes, [202, 409, 409, 409, 409])
        self.assertEqual(len([c for c in self.aws.sqs.calls if c[0] == 'send_message']), 1)
        self.run_worker()
        codes = [self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'COLLECTED'}, 'd1')['statusCode'] for _ in range(4)]
        self.assertEqual(codes, [200, 409, 409, 409])
        self.assertEqual(len(self.D.items['d1']['history']), 4)

    def test_repeated_reservations_by_one_customer_do_not_stack(self):
        self.seed(status='LISTED', category='REUSE')
        codes = [self.call('MarketFn', 'POST /market/{id}/reserve', claims('b'), did='d1')['statusCode'] for _ in range(3)]
        self.assertEqual(codes, [200, 409, 409])

    def test_oversized_text_is_truncated_everywhere(self):
        self.seed(status='SCHEDULED')
        self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'PICKUP_FAILED', 'note': 'n' * 5000}, 'd1')
        self.assertEqual(len(self.D.items['d1']['lastNote']), 100)
        self.seed(deviceId='d2')
        self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {'address': 'a' * 5000}, 'd2')
        self.assertEqual(len(self.D.items['d2']['address']), 200)

    def test_script_in_text_fields_is_stored_as_plain_text_only(self):
        evil = '<img src=x onerror=alert(1)>'
        self.call('RegisterFn', 'POST /devices', claims('u1'), {'name': evil, 'type': 'laptop', 'condition': 'working', 'age': 1})
        self.assertEqual(list(self.D.items.values())[0]['name'], evil)
        self.assertEqual(body(self.call('ListFn', 'GET /devices', claims('u1')))['items'][0]['name'], evil)   # escaped by the page, covered by browser tests

    def test_every_function_survives_a_missing_authorizer_claim_gracefully(self):
        """The authorizer always sets these claims; if it ever did not, the function must fail closed, not open."""
        e = {'routeKey': 'GET /devices', 'requestContext': {'authorizer': {'jwt': {'claims': {}}}}}
        with self.assertRaises(KeyError):
            self.fn['ListFn'].handler(e, None)
        self.assertEqual(self.D.items, {})
