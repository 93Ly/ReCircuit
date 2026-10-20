"""The status workflow, the audit trail, retention and complete device journeys."""
import itertools
import time

from base import FINAL, ROLES, STATUSES, Base, body, claims, stream_event

# The workflow rules written out independently of the code under test. This is the spec.
ALLOWED = {('SCHEDULED', 'COLLECTED'): 'collectors', ('SCHEDULED', 'PICKUP_FAILED'): 'collectors',
           ('COLLECTED', 'IN_REPAIR'): 'repairers', ('COLLECTED', 'PROCESSING'): 'processors',
           ('IN_REPAIR', 'REPAIRED'): 'repairers', ('IN_REPAIR', 'UNREPAIRABLE'): 'repairers',
           ('UNREPAIRABLE', 'PROCESSING'): 'processors', ('REPAIRED', 'RETURNED'): 'collectors',
           ('PROCESSING', 'REUSED'): 'processors', ('PROCESSING', 'RECYCLED'): 'processors',
           ('PROCESSING', 'LISTED'): 'processors', ('LISTED', 'PROCESSING'): 'processors',
           ('RESERVED', 'REUSED'): 'processors', ('RESERVED', 'LISTED'): 'processors'}


class StatusMatrix(Base):
    def test_full_transition_matrix(self):
        """Every (from, to) pair x role x category: only the owning role may move a device; nothing changes otherwise."""
        checked = 0
        for frm, to, (role, groups), cat in itertools.product(STATUSES, STATUSES, ROLES.items(), ['REPAIR', 'REUSE', 'RECYCLE']):
            self.seed(status=frm, category=cat)
            r = self.call('StatusFn', 'PATCH /devices/{id}/status', claims('actor', groups), {'status': to, 'note': 'customer not at home'}, 'd1')
            owner = ALLOWED.get((frm, to))
            if owner is None:
                want = 409
            elif owner != role:
                want = 403
            elif (to == 'IN_REPAIR' and cat != 'REPAIR') or (frm == 'COLLECTED' and to == 'PROCESSING' and cat == 'REPAIR') \
                    or (frm == 'PROCESSING' and to == 'LISTED' and cat == 'RECYCLE'):
                want = 409
            else:
                want = 200
            self.assertEqual(r['statusCode'], want, f'{role} {frm}->{to} ({cat})')
            now = self.D.items['d1']
            if want == 200:
                last = now['history'][-1]
                self.assertEqual((now['status'], last['s'], last['f'], last['r'], last['by']), (to, to, frm, role, 'actor'))
                self.assertEqual(len(now['history']), 2)
            else:
                self.assertEqual(now['status'], frm, 'a rejected change must not modify the device')
                self.assertEqual(len(now['history']), 1, 'and must not write an audit entry')
            checked += 1
        self.assertEqual(checked, 14 * 14 * 4 * 3)

    def test_the_customer_cannot_move_their_own_device(self):
        for frm, to in ALLOWED:
            self.seed(status=frm)
            self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', claims('u1'), {'status': to, 'note': 'x'}, 'd1')['statusCode'], 403, (frm, to))

    def test_role_claim_formats_from_api_gateway(self):
        for fmt in ('[collectors]', 'collectors', '[repairers collectors]'):
            self.seed(status='SCHEDULED')
            cl = {'sub': 'c', 'email': 'c@x.com', 'cognito:groups': fmt}
            self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', cl, {'status': 'COLLECTED'}, 'd1')['statusCode'], 200, fmt)

    def test_a_group_name_inside_another_word_grants_nothing(self):
        self.seed(status='SCHEDULED')
        cl = {'sub': 'c', 'email': 'c@x.com', 'cognito:groups': '[notcollectors]'}
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', cl, {'status': 'COLLECTED'}, 'd1')['statusCode'], 403)

    def test_unknown_device(self):
        r = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'COLLECTED'}, 'nope')
        self.assertEqual(r['statusCode'], 404)

    def test_stale_write_loses_the_race(self):
        """Two staff act on the same device at once: the second write must be rejected, not silently applied."""
        d = self.seed(status='SCHEDULED')
        real_get = self.D.get_item
        stale = real_get(Key={'deviceId': 'd1'})
        # a competing change lands between the read and the write
        self.D.items['d1']['status'] = 'COLLECTED'
        self.D.get_item = lambda Key: stale
        r = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'COLLECTED'}, 'd1')
        self.assertEqual(r['statusCode'], 409)
        self.assertEqual(len(self.D.items['d1']['history']), 1)


class AuditTrail(Base):
    def test_every_change_records_who_what_and_when(self):
        self.seed(status='SCHEDULED')
        before = int(time.time())
        self.call('StatusFn', 'PATCH /devices/{id}/status', claims('coll-42', ['collectors']), {'status': 'COLLECTED', 'note': 'picked up at gate'}, 'd1')
        e = self.D.items['d1']['history'][-1]
        self.assertEqual((e['by'], e['r'], e['f'], e['s'], e['n']), ('coll-42', 'collectors', 'SCHEDULED', 'COLLECTED', 'picked up at gate'))
        self.assertGreaterEqual(e['t'], before)

    def test_actor_ids_are_stored_but_never_returned_by_the_api(self):
        self.seed(status='SCHEDULED')
        self.call('StatusFn', 'PATCH /devices/{id}/status', claims('coll-42', ['collectors']), {'status': 'COLLECTED'}, 'd1')
        self.assertEqual(self.D.items['d1']['history'][-1]['by'], 'coll-42')
        for cl in (claims('u1'), self.staff('collectors')):
            for item in body(self.call('ListFn', 'GET /devices', cl))['items']:
                for h in item['history']:
                    self.assertNotIn('by', h)
                    self.assertIn('r', h)

    def test_notes_are_trimmed_and_typed(self):
        self.seed(status='SCHEDULED')
        self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'PICKUP_FAILED', 'note': 'x' * 500}, 'd1')
        self.assertEqual(len(self.D.items['d1']['history'][-1]['n']), 100)

    def test_customer_actions_are_audited_too(self):
        self.seed()
        self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {'address': '1 Main St'}, 'd1')
        e = self.D.items['d1']['history'][-1]
        self.assertEqual((e['by'], e['r'], e['f'], e['s']), ('u1', 'user', 'REGISTERED', 'PICKUP_REQUESTED'))
        self.run_worker()
        e = self.D.items['d1']['history'][-1]
        self.assertEqual((e['by'], e['r'], e['s']), ('system', 'system', 'SCHEDULED'))


class Retention(Base):
    def test_finished_devices_get_a_deletion_date_one_year_out(self):
        for frm, to in (('REPAIRED', 'RETURNED'), ('PROCESSING', 'REUSED'), ('PROCESSING', 'RECYCLED'), ('RESERVED', 'REUSED')):
            self.seed(status=frm)
            self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors' if to == 'RETURNED' else 'processors'), {'status': to}, 'd1')
            exp = int(self.D.items['d1']['expiresAt'])
            self.assertAlmostEqual(exp, time.time() + 365 * 86400, delta=60, msg=to)

    def test_unfinished_devices_are_kept(self):
        self.seed(status='COLLECTED')
        self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('processors'), {'status': 'PROCESSING'}, 'd1')
        self.assertNotIn('expiresAt', self.D.items['d1'])

    def test_requests_and_slots_expire_too(self):
        self.seed()
        self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {'address': 'x'}, 'd1')
        self.assertIn('expiresAt', self.R.items['d1'])
        self.run_worker()
        self.assertTrue(all('expiresAt' in s for s in self.S.items.values()))

    def test_api_never_exposes_the_deletion_date(self):
        self.seed(status='RETURNED', expiresAt=2000000000)
        self.assertNotIn('expiresAt', body(self.call('ListFn', 'GET /devices', claims('u1')))['items'][0])


class Journeys(Base):
    def go(self, category, steps, name='Dell'):
        u = claims('cust', email='cust@x.com')
        r = body(self.call('RegisterFn', 'POST /devices', u, {'name': name, 'type': 'laptop', 'condition': 'working', 'age': 2, 'category': category}))
        did = r['deviceId']
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', u, {'address': '1 Main St'}, did)['statusCode'], 202)
        self.run_worker()
        for role, to in steps:
            res = self.call('StatusFn', 'PATCH /devices/{id}/status', claims(role + '1', [role]), {'status': to, 'note': 'ok'}, did)
            self.assertEqual(res['statusCode'], 200, f'{role} -> {to}: {res["body"]}')
        return self.D.items[did], did

    def emails(self, statuses):
        self.fn['NotifierFn'].handler(stream_event(self.aws.stream), None)
        pub = [c[1] for c in self.aws.sns.calls if c[0] == 'publish']
        for s in statuses:
            self.assertTrue(any(s.replace('_', ' ').title() in p['Message'] for p in pub), 'no email for ' + s)
        return pub

    def test_repair_and_return(self):
        d, _ = self.go('REPAIR', [('collectors', 'COLLECTED'), ('repairers', 'IN_REPAIR'), ('repairers', 'REPAIRED'), ('collectors', 'RETURNED')])
        self.assertEqual([h['s'] for h in d['history']], ['REGISTERED', 'PICKUP_REQUESTED', 'SCHEDULED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED', 'RETURNED'])
        self.emails(['SCHEDULED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED', 'RETURNED'])

    def test_recycle(self):
        d, _ = self.go('RECYCLE', [('collectors', 'COLLECTED'), ('processors', 'PROCESSING'), ('processors', 'RECYCLED')])
        self.assertEqual(d['status'], 'RECYCLED')

    def test_direct_reuse(self):
        d, _ = self.go('REUSE', [('collectors', 'COLLECTED'), ('processors', 'PROCESSING'), ('processors', 'REUSED')])
        self.assertEqual(d['status'], 'REUSED')

    def test_unrepairable_falls_through_to_processors(self):
        d, did = self.go('REPAIR', [('collectors', 'COLLECTED'), ('repairers', 'IN_REPAIR'), ('repairers', 'UNREPAIRABLE'), ('processors', 'PROCESSING'), ('processors', 'RECYCLED')])
        self.assertEqual(d['status'], 'RECYCLED')

    def test_repair_devices_cannot_skip_the_repair_bench(self):
        d, did = self.go('REPAIR', [('collectors', 'COLLECTED')])
        r = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('processors'), {'status': 'PROCESSING'}, did)
        self.assertEqual(r['statusCode'], 409)

    def test_marketplace_reuse_journey(self):
        d, did = self.go('REUSE', [('collectors', 'COLLECTED'), ('processors', 'PROCESSING'), ('processors', 'LISTED')])
        buyer = claims('buyer')
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', buyer, did=did)['statusCode'], 200)
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/release', buyer, did=did)['statusCode'], 200)
        self.assertEqual(self.D.items[did]['status'], 'LISTED')
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', buyer, did=did)['statusCode'], 200)
        res = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('processors'), {'status': 'REUSED', 'note': 'handed over'}, did)
        self.assertEqual(res['statusCode'], 200)
        self.assertEqual(self.D.items[did]['status'], 'REUSED')
        self.assertEqual([h['s'] for h in self.D.items[did]['history']][-5:], ['LISTED', 'RESERVED', 'LISTED', 'RESERVED', 'REUSED'])
        # the original owner sees the outcome but not who took it
        owner_view = body(self.call('ListFn', 'GET /devices', claims('cust')))['items'][0]
        self.assertEqual(owner_view['status'], 'REUSED')
        self.assertNotIn('reservedBy', owner_view)
        self.emails(['LISTED', 'RESERVED', 'REUSED'])

    def test_failed_pickup_then_a_new_slot(self):
        u = claims('cust')
        did = body(self.call('RegisterFn', 'POST /devices', u, {'name': 'Phone', 'type': 'phone', 'condition': 'working', 'age': 1}))['deviceId']
        self.call('PickupFn', 'POST /devices/{id}/pickup', u, {'address': '9 Hill Rd'}, did)
        self.run_worker()
        first = self.D.items[did]['slot']
        self.assertEqual(self.S.items[first]['booked'], 1)
        res = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'PICKUP_FAILED', 'note': 'nobody home'}, did)
        self.assertEqual(res['statusCode'], 200)
        d = self.D.items[did]
        self.assertEqual((d['status'], d['failedAttempts'], d['lastNote']), ('PICKUP_FAILED', 1, 'nobody home'))
        self.assertNotIn('slot', d)
        self.assertEqual(self.S.items[first]['booked'], 0, 'the slot goes back to the pool')
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', u, {}, did)['statusCode'], 202)   # re-uses the saved address
        self.assertEqual(self.D.items[did]['address'], '9 Hill Rd')
        self.run_worker()
        self.assertEqual(self.D.items[did]['status'], 'SCHEDULED')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'COLLECTED'}, did)['statusCode'], 200)
        pub = self.emails(['PICKUP_FAILED'])
        self.assertTrue(any('nobody home' in p['Message'] for p in pub))
