"""The new features: smart triage, slot booking with capacity, reschedule/cancel, failed pickups, marketplace, notifications."""
import datetime as dt
import itertools
import time

from base import STATUSES, Base, body, claims, device, stream_event

TYPES = ('laptop', 'phone', 'tablet', 'appliance', 'other')


# ======================================================================= smart device triage
class Triage(Base):
    def assess(self, t, c, age):
        r = self.call('RegisterFn', 'POST /assess', claims('u1'), {'type': t, 'condition': c, 'age': age})
        self.assertEqual(r['statusCode'], 200, (t, c, age))
        return body(r)

    def test_rules_table(self):
        """The rule set as the user reads it: condition and age decide, batteries are always recycled."""
        for t, c, age, want in [
            ('laptop', 'working', 0, 'REUSE'), ('laptop', 'working', 8, 'REUSE'), ('laptop', 'working', 9, 'RECYCLE'),
            ('phone', 'needs repair', 0, 'REPAIR'), ('phone', 'needs repair', 6, 'REPAIR'), ('phone', 'needs repair', 7, 'RECYCLE'),
            ('tablet', 'broken', 0, 'REPAIR'), ('tablet', 'broken', 3, 'REPAIR'), ('tablet', 'broken', 4, 'RECYCLE'),
            ('appliance', 'working', 5, 'REUSE'), ('other', 'broken', 20, 'RECYCLE'),
            ('battery', 'working', 0, 'RECYCLE'), ('battery', 'needs repair', 1, 'RECYCLE'), ('battery', 'broken', 40, 'RECYCLE')]:
            self.assertEqual(self.assess(t, c, age)['recommended'], want, (t, c, age))

    def test_every_recommendation_explains_itself(self):
        for t, c, age in itertools.product(TYPES + ('battery',), ('working', 'needs repair', 'broken'), (0, 3, 6, 8, 15, 40)):
            a = self.assess(t, c, age)
            self.assertIn(a['recommended'], ('REUSE', 'REPAIR', 'RECYCLE'))
            self.assertGreater(len(a['reason']), 20)

    def test_the_assessment_is_stored_with_the_device(self):
        r = self.call('RegisterFn', 'POST /devices', claims('u1'), {'name': 'Old phone', 'type': 'phone', 'condition': 'needs repair', 'age': 2, 'category': 'REPAIR'})
        self.assertEqual(r['statusCode'], 201)
        d = list(self.D.items.values())[0]
        self.assertEqual(d['assessment']['recommended'], 'REPAIR')
        self.assertEqual((d['age'], d['category']), (2, 'REPAIR'))

    def test_category_defaults_to_the_recommendation_and_can_be_overridden(self):
        base = {'name': 'A', 'type': 'laptop', 'condition': 'working', 'age': 12}
        self.call('RegisterFn', 'POST /devices', claims('u1'), base)
        self.assertEqual(list(self.D.items.values())[0]['category'], 'RECYCLE')
        self.call('RegisterFn', 'POST /devices', claims('u1'), dict(base, category='REUSE'))
        cats = sorted(d['category'] for d in self.D.items.values())
        self.assertEqual(cats, ['RECYCLE', 'REUSE'])
        stored = [d for d in self.D.items.values() if d['category'] == 'REUSE'][0]
        self.assertEqual(stored['assessment']['recommended'], 'RECYCLE', 'the recommendation is kept even when overridden')

    def test_assess_does_not_store_anything(self):
        self.assess('laptop', 'working', 3)
        self.assertEqual(self.D.items, {})

    def test_bad_inputs_are_rejected(self):
        for bad in ({}, {'type': 'laptop'}, {'type': 'toaster', 'condition': 'working', 'age': 1}, {'type': 'laptop', 'condition': 'mint', 'age': 1},
                    {'type': 'laptop', 'condition': 'working', 'age': -1}, {'type': 'laptop', 'condition': 'working', 'age': 41},
                    {'type': 'laptop', 'condition': 'working', 'age': 2.5}, {'type': 'laptop', 'condition': 'working', 'age': '3'},
                    {'type': 'laptop', 'condition': 'working', 'age': True}, {'type': ['laptop'], 'condition': 'working', 'age': 1}):
            self.assertEqual(self.call('RegisterFn', 'POST /assess', claims('u1'), bad)['statusCode'], 400, bad)


# ======================================================================= registering and listing
class Register(Base):
    OK = {'name': 'Dell', 'type': 'laptop', 'condition': 'working', 'age': 3}

    def test_customer_registers_a_device(self):
        r = self.call('RegisterFn', 'POST /devices', claims('u1'), self.OK)
        self.assertEqual(r['statusCode'], 201)
        d = list(self.D.items.values())[0]
        self.assertEqual((d['userId'], d['status'], d['history'][0]['s'], d['history'][0]['r']), ('u1', 'REGISTERED', 'REGISTERED', 'user'))

    def test_validation(self):
        for patch in ({'name': ''}, {'name': '   '}, {'name': 7}, {'name': None}, {'category': 'BURN'}, {'category': ['REUSE']}, {'photoKey': 5}):
            self.assertEqual(self.call('RegisterFn', 'POST /devices', claims('u1'), dict(self.OK, **patch))['statusCode'], 400, patch)
        self.assertEqual(self.D.items, {})

    def test_name_is_truncated(self):
        self.call('RegisterFn', 'POST /devices', claims('u1'), dict(self.OK, name='x' * 500))
        self.assertEqual(len(list(self.D.items.values())[0]['name']), 100)

    def test_staff_cannot_register_devices_or_upload_photos(self):
        for role in ('collectors', 'repairers', 'processors'):
            for route in ('POST /devices', 'POST /upload-url', 'POST /assess'):
                self.assertEqual(self.call('RegisterFn', route, self.staff(role), self.OK)['statusCode'], 403, (role, route))
        self.assertEqual(self.D.items, {})

    def test_upload_url_uses_a_random_key_without_the_user_id_and_expires(self):
        r = body(self.call('RegisterFn', 'POST /upload-url', claims('u7'), {'contentType': 'image/png'}))
        self.assertRegex(r['photoKey'], r'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
        self.assertNotIn('u7', r['uploadUrl'])
        self.assertIn('exp=300', r['uploadUrl'])
        self.assertEqual(r['contentType'], 'image/png')

    def test_only_jpeg_and_png_uploads_are_signed(self):
        for ct in ('text/html', 'application/x-sh', 'image/svg+xml', None, 5):
            self.assertEqual(body(self.call('RegisterFn', 'POST /upload-url', claims('u1'), {'contentType': ct}))['contentType'], 'image/jpeg', ct)


class Listing(Base):
    def test_customers_only_see_their_own_devices(self):
        self.seed(deviceId='a', userId='u1')
        self.seed(deviceId='b', userId='u2')
        got = body(self.call('ListFn', 'GET /devices', claims('u1')))
        self.assertEqual([i['deviceId'] for i in got['items']], ['a'])
        self.assertEqual(got['role'], 'user')

    def test_photo_gets_a_short_lived_signed_url(self):
        self.seed(photoKey='11111111-1111-4111-8111-111111111111')
        item = body(self.call('ListFn', 'GET /devices', claims('u1')))['items'][0]
        self.assertIn('11111111-1111-4111-8111-111111111111', item['photoUrl'])
        self.assertNotIn('photoKey', item, 'only the signed link is returned, never the raw key')
        self.assertIn('exp=300', item['photoUrl'])

    def test_each_staff_role_sees_only_its_queue(self):
        for status, cat in itertools.product(STATUSES, ['REPAIR', 'REUSE', 'RECYCLE']):
            self.seed(deviceId=f'{status}-{cat}', status=status, category=cat, address='1 Main St')
        base = {'collectors': {'SCHEDULED', 'PICKUP_FAILED', 'COLLECTED', 'REPAIRED', 'RETURNED'},
                'repairers': {'IN_REPAIR', 'REPAIRED', 'UNREPAIRABLE'},
                'processors': {'UNREPAIRABLE', 'PROCESSING', 'LISTED', 'RESERVED', 'REUSED', 'RECYCLED'}}
        for role, statuses in base.items():
            got = body(self.call('ListFn', 'GET /devices', self.staff(role)))
            want = {f'{s}-{c}' for s in statuses for c in ('REPAIR', 'REUSE', 'RECYCLE')}
            if role == 'repairers':
                want.add('COLLECTED-REPAIR')
            if role == 'processors':
                want |= {'COLLECTED-REUSE', 'COLLECTED-RECYCLE'}
            self.assertEqual({i['deviceId'] for i in got['items']}, want, role)
            self.assertEqual(got['role'], role)

    def test_sensitive_fields_are_never_returned(self):
        self.seed(status='SCHEDULED', address='221B Baker St', reservedBy='u9', expiresAt=2000000000)
        for cl in (claims('u1'), self.staff('collectors')):
            item = body(self.call('ListFn', 'GET /devices', cl))['items'][0]
            for k in ('userId', 'reservedBy', 'expiresAt'):
                self.assertNotIn(k, item, k)

    def test_only_customers_and_collectors_see_addresses(self):
        self.seed(status='SCHEDULED', address='221B Baker St')
        self.seed(deviceId='r', status='IN_REPAIR', address='221B Baker St')
        self.seed(deviceId='p', status='PROCESSING', address='221B Baker St')
        self.assertEqual(body(self.call('ListFn', 'GET /devices', self.staff('collectors')))['items'][0]['address'], '221B Baker St')
        self.assertEqual(body(self.call('ListFn', 'GET /devices', claims('u1')))['items'][0]['address'], '221B Baker St')
        for role in ('repairers', 'processors'):
            for i in body(self.call('ListFn', 'GET /devices', self.staff(role)))['items']:
                self.assertNotIn('address', i, role)


# ======================================================================= pickup slots
class Slots(Base):
    CAP = 2

    def request(self, did, uid='u1', address='1 Main St'):
        return self.call('PickupFn', 'POST /devices/{id}/pickup', claims(uid), {'address': address}, did)

    def test_pickup_request_flow(self):
        self.seed()
        r = self.request('d1', address='  12 High St  ')
        self.assertEqual(r['statusCode'], 202)
        d = self.D.items['d1']
        self.assertEqual((d['status'], d['address']), ('PICKUP_REQUESTED', '12 High St'))
        self.assertEqual(self.R.items['d1']['status'], 'PENDING')
        self.assertEqual(len([c for c in self.aws.sqs.calls if c[0] == 'send_message']), 1)

    def test_slots_are_assigned_up_to_capacity_then_the_next_slot(self):
        for i in range(5):
            self.seed(deviceId=f'd{i}', userId=f'u{i}')
            self.request(f'd{i}', f'u{i}')
        self.run_worker()
        booked = {s: v['booked'] for s, v in self.S.items.items()}
        self.assertTrue(all(n <= self.CAP for n in booked.values()), booked)
        self.assertEqual(sorted(booked.values(), reverse=True), [2, 2, 1])
        order = self.slots()
        self.assertEqual([d['slot'] for _, d in sorted(self.D.items.items())], [order[0], order[0], order[1], order[1], order[2]])

    def test_two_requests_cannot_claim_the_last_seat(self):
        take = self.fn['WorkerFn'].take
        s = self.slots()[0]
        self.S.items[s] = {'slotId': s, 'booked': self.CAP - 1}
        self.assertTrue(take(s))
        self.assertFalse(take(s), 'the slot is now full')
        self.assertEqual(self.S.items[s]['booked'], self.CAP)

    def test_a_full_week_raises_so_sqs_retries_and_alerts(self):
        for s in self.slots():
            self.S.items[s] = {'slotId': s, 'booked': self.CAP}
        self.seed()
        self.request('d1')
        with self.assertRaises(RuntimeError):
            self.run_worker()
        self.assertEqual(self.D.items['d1']['status'], 'PICKUP_REQUESTED')

    def test_duplicate_queue_messages_do_not_double_book(self):
        self.seed()
        self.request('d1')
        msg = [c[1] for c in self.aws.sqs.calls if c[0] == 'send_message'][0]
        ev = {'Records': [{'body': msg['MessageBody']}, {'body': msg['MessageBody']}]}
        self.fn['WorkerFn'].handler(ev, None)
        self.assertEqual(sum(v['booked'] for v in self.S.items.values()), 1)
        self.assertEqual([h['s'] for h in self.D.items['d1']['history']].count('SCHEDULED'), 1)

    def test_message_for_a_cancelled_or_missing_device_is_ignored(self):
        self.fn['WorkerFn'].handler({'Records': [{'body': '{"deviceId": "ghost"}'}]}, None)
        self.seed(status='REGISTERED')
        self.fn['WorkerFn'].handler({'Records': [{'body': '{"deviceId": "d1"}'}]}, None)
        self.assertEqual(self.S.items, {})

    def test_get_slots_hides_full_ones(self):
        full = self.slots()[0]
        self.S.items[full] = {'slotId': full, 'booked': self.CAP}
        self.S.items[self.slots()[1]] = {'slotId': self.slots()[1], 'booked': 1}
        got = body(self.call('PickupFn', 'GET /slots', claims('u1')))['slots']
        names = [s['slot'] for s in got]
        self.assertNotIn(full, names)
        self.assertEqual(len(names), 20)
        self.assertEqual([s['left'] for s in got if s['slot'] == self.slots()[1]], [1])

    def test_pickup_rules(self):
        self.seed()
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {}, 'd1')['statusCode'], 400, 'address required')
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {'address': '  '}, 'd1')['statusCode'], 400)
        self.assertEqual(self.request('d1', address='a' * 999)['statusCode'], 202)
        self.assertEqual(len(self.D.items['d1']['address']), 200)
        self.assertEqual(self.request('d1')['statusCode'], 409, 'only once')

    def test_staff_cannot_request_pickups(self):
        self.seed(userId='s1')
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', self.staff('collectors', 's1'), {'address': 'x'}, 'd1')['statusCode'], 403)


class Change(Base):
    def scheduled(self, hours_ahead=None, **kw):
        slot = self.slots()[3]
        if hours_ahead is not None:
            when = dt.datetime.utcnow() + dt.timedelta(hours=hours_ahead)
            slot = when.strftime('%Y-%m-%d %H:%M') + '-23:59'
        self.S.items[slot] = {'slotId': slot, 'booked': 1}
        return self.seed(status='SCHEDULED', slot=slot, address='1 Main St', **kw)

    def test_reschedule_moves_the_booking(self):
        d = self.scheduled()
        new = self.slots()[5]
        r = self.call('ChangeFn', 'POST /devices/{id}/reschedule', claims('u1'), {'slot': new}, 'd1')
        self.assertEqual(r['statusCode'], 200)
        now = self.D.items['d1']
        self.assertEqual((now['status'], now['slot']), ('SCHEDULED', new))
        self.assertEqual(self.S.items[new]['booked'], 1)
        self.assertEqual(self.S.items[d['slot']]['booked'], 0)
        e = now['history'][-1]
        self.assertEqual((e['n'], e['r'], e['by']), ('rescheduled', 'user', 'u1'))

    def test_reschedule_into_a_full_slot_is_refused(self):
        self.scheduled()
        full = self.slots()[5]
        self.S.items[full] = {'slotId': full, 'booked': self.CAP}
        r = self.call('ChangeFn', 'POST /devices/{id}/reschedule', claims('u1'), {'slot': full}, 'd1')
        self.assertEqual(r['statusCode'], 409)
        self.assertEqual(self.S.items[full]['booked'], self.CAP)

    def test_bad_reschedule_requests(self):
        d = self.scheduled()
        for slot in (None, 5, '', 'tomorrow', '2020-01-01 09:00-12:00', d['slot']):
            self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/reschedule', claims('u1'), {'slot': slot}, 'd1')['statusCode'], 400, slot)

    def test_cutoff_blocks_changes_close_to_the_slot(self):
        self.scheduled(hours_ahead=2)
        for route, payload in (('POST /devices/{id}/reschedule', {'slot': self.slots()[5]}), ('POST /devices/{id}/cancel', {})):
            r = self.call('ChangeFn', route, claims('u1'), payload, 'd1')
            self.assertEqual(r['statusCode'], 409, route)
            self.assertIn('cutoff', body(r)['error'])
        self.assertEqual(self.D.items['d1']['status'], 'SCHEDULED')

    def test_changes_just_outside_the_cutoff_are_allowed(self):
        self.scheduled(hours_ahead=25)
        self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/cancel', claims('u1'), {}, 'd1')['statusCode'], 200)

    def test_cancel_returns_the_device_to_registered_and_frees_the_slot(self):
        d = self.scheduled()
        r = self.call('ChangeFn', 'POST /devices/{id}/cancel', claims('u1'), {}, 'd1')
        self.assertEqual(r['statusCode'], 200)
        now = self.D.items['d1']
        self.assertEqual(now['status'], 'REGISTERED')
        self.assertNotIn('slot', now)
        self.assertEqual(self.S.items[d['slot']]['booked'], 0)
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {'address': 'x'}, 'd1')['statusCode'], 202, 'they can book again')

    def test_only_the_owner_and_only_scheduled_pickups(self):
        self.scheduled()
        self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/cancel', claims('intruder'), {}, 'd1')['statusCode'], 404)
        self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/cancel', self.staff('collectors', 'u1'), {}, 'd1')['statusCode'], 403)
        for st in ('REGISTERED', 'PICKUP_REQUESTED', 'COLLECTED', 'PICKUP_FAILED'):
            self.D.items['d1']['status'] = st
            self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/cancel', claims('u1'), {}, 'd1')['statusCode'], 409, st)

    def test_a_stale_cancel_releases_nothing(self):
        d = self.scheduled()
        stale = self.D.get_item(Key={'deviceId': 'd1'})
        self.D.items['d1']['status'] = 'COLLECTED'
        self.D.get_item = lambda Key: stale
        self.assertEqual(self.call('ChangeFn', 'POST /devices/{id}/cancel', claims('u1'), {}, 'd1')['statusCode'], 409)
        self.assertEqual(self.S.items[d['slot']]['booked'], 1)


class FailedPickups(Base):
    def test_a_failed_pickup_needs_a_reason(self):
        self.seed(status='SCHEDULED')
        for note in (None, '', '   ', 5):
            r = self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'PICKUP_FAILED', 'note': note}, 'd1')
            self.assertEqual(r['statusCode'], 400, note)
        self.assertEqual(self.D.items['d1']['status'], 'SCHEDULED')

    def test_the_customer_can_try_again_up_to_three_failures(self):
        for attempt in range(1, 4):
            self.seed(status='SCHEDULED', failedAttempts=attempt - 1, address='1 Main St')
            self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('collectors'), {'status': 'PICKUP_FAILED', 'note': 'no answer'}, 'd1')
            self.assertEqual(self.D.items['d1']['failedAttempts'], attempt)
            expected = 202 if attempt < 3 else 409
            self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u1'), {}, 'd1')['statusCode'], expected, attempt)

    def test_only_the_owner_can_rebook(self):
        self.seed(status='PICKUP_FAILED', address='1 Main St')
        self.assertEqual(self.call('PickupFn', 'POST /devices/{id}/pickup', claims('u2'), {}, 'd1')['statusCode'], 404)


# ======================================================================= marketplace
class Market(Base):
    def listed(self, **kw):
        base = dict(status='LISTED', category='REUSE', type='laptop', condition='working', age=3, address='1 Main St', photoKey='11111111-1111-4111-8111-111111111111')
        base.update(kw)
        return self.seed(**base)

    def items(self, uid='buyer', query=None):
        r = self.call('MarketFn', 'GET /market', claims(uid), query=query)
        self.assertEqual(r['statusCode'], 200)
        return body(r)['items']

    def test_listing_requires_a_processor_and_a_non_recycle_plan(self):
        self.seed(status='PROCESSING', category='REUSE')
        for role in ('collectors', 'repairers'):
            self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff(role), {'status': 'LISTED'}, 'd1')['statusCode'], 403)
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', claims('u1'), {'status': 'LISTED'}, 'd1')['statusCode'], 403)
        self.seed(status='PROCESSING', category='RECYCLE')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('processors'), {'status': 'LISTED'}, 'd1')['statusCode'], 409)
        self.seed(status='PROCESSING', category='REPAIR')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', self.staff('processors'), {'status': 'LISTED'}, 'd1')['statusCode'], 200)
        self.assertIn('listedAt', self.D.items['d1'])

    def test_a_device_cannot_be_listed_twice(self):
        self.seed(status='PROCESSING', category='REUSE')
        p = self.staff('processors')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', p, {'status': 'LISTED'}, 'd1')['statusCode'], 200)
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', p, {'status': 'LISTED'}, 'd1')['statusCode'], 409)
        self.assertEqual([h['s'] for h in self.D.items['d1']['history']].count('LISTED'), 1)
        self.assertEqual(len(self.items()), 1)

    def test_browse_shows_only_listed_items_and_no_owner_information(self):
        self.listed(deviceId='a', name='Dell XPS')
        self.seed(deviceId='b', status='PROCESSING')
        self.seed(deviceId='c', status='REGISTERED')
        got = self.items()
        self.assertEqual([i['deviceId'] for i in got], ['a'])
        item = got[0]
        self.assertEqual(set(item), {'deviceId', 'name', 'type', 'condition', 'age', 'category', 'status', 'mine', 'photoUrl'})
        self.assertNotIn('u1', str(item).replace('11111111-1111-4111-8111-111111111111', ''), 'the owner id must not leak')
        self.assertNotIn('Main St', str(item), 'the address must not leak')

    def test_filters(self):
        self.listed(deviceId='a', type='laptop', condition='working')
        self.listed(deviceId='b', type='phone', condition='working')
        self.listed(deviceId='c', type='phone', condition='needs repair', category='REPAIR')
        ids = lambda q: sorted(i['deviceId'] for i in self.items(query=q))
        self.assertEqual(ids({'type': 'phone'}), ['b', 'c'])
        self.assertEqual(ids({'condition': 'working'}), ['a', 'b'])
        self.assertEqual(ids({'type': 'phone', 'condition': 'working'}), ['b'])
        self.assertEqual(ids({'category': 'REPAIR'}), ['c'])
        self.assertEqual(ids(None), ['a', 'b', 'c'])
        for bad in ({'type': 'toaster'}, {'condition': 'mint'}, {'category': 'x'}):
            self.assertEqual(self.call('MarketFn', 'GET /market', claims('buyer'), query=bad)['statusCode'], 400, bad)

    def test_reserve_and_release(self):
        self.listed()
        r = self.call('MarketFn', 'POST /market/{id}/reserve', claims('buyer'), did='d1')
        self.assertEqual(r['statusCode'], 200)
        self.assertEqual((self.D.items['d1']['status'], self.D.items['d1']['reservedBy']), ('RESERVED', 'buyer'))
        mine = self.items('buyer')
        self.assertEqual([(i['status'], i['mine']) for i in mine], [('RESERVED', True)])
        self.assertEqual(self.items('someone-else'), [], 'reserved items disappear for everyone else')
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/release', claims('someone-else'), did='d1')['statusCode'], 409)
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/release', claims('buyer'), did='d1')['statusCode'], 200)
        self.assertNotIn('reservedBy', self.D.items['d1'])
        self.assertEqual(len(self.items('someone-else')), 1)

    def test_only_one_customer_wins_a_contested_item(self):
        self.listed()
        stale = self.D.get_item(Key={'deviceId': 'd1'})
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', claims('first'), did='d1')['statusCode'], 200)
        self.D.get_item = lambda Key: stale            # the second customer read the item before the first reserved it
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', claims('second'), did='d1')['statusCode'], 409)
        self.assertEqual(self.D.items['d1']['reservedBy'], 'first')

    def test_owners_cannot_reserve_their_own_device_and_staff_cannot_reserve(self):
        self.listed()
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', claims('u1'), did='d1')['statusCode'], 403)
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', self.staff('processors'), did='d1')['statusCode'], 403)
        self.assertEqual(self.D.items['d1']['status'], 'LISTED')

    def test_unavailable_items_cannot_be_reserved(self):
        for st in ('PROCESSING', 'RESERVED', 'REUSED', 'REGISTERED'):
            self.seed(status=st)
            self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', claims('buyer'), did='d1')['statusCode'], 409, st)
        self.assertEqual(self.call('MarketFn', 'POST /market/{id}/reserve', claims('buyer'), did='ghost')['statusCode'], 404)

    def test_processors_manage_reservations(self):
        self.listed(status='RESERVED', reservedBy='buyer')
        p = self.staff('processors')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', p, {'status': 'LISTED'}, 'd1')['statusCode'], 200, 'release back to available')
        self.assertNotIn('reservedBy', self.D.items['d1'])
        self.D.items['d1'].update(status='RESERVED', reservedBy='buyer')
        self.assertEqual(self.call('StatusFn', 'PATCH /devices/{id}/status', p, {'status': 'REUSED'}, 'd1')['statusCode'], 200, 'handover complete')
        self.assertEqual([(i['status'], i['mine']) for i in self.items('buyer')], [('REUSED', True)], 'the outcome stays visible to the reserver')
        self.assertEqual(self.items('other'), [])


# ======================================================================= notifications
class Notifications(Base):
    def publishes(self):
        return [c[1] for c in self.aws.sns.calls if c[0] == 'publish']

    def change(self, old, new, **kw):
        self.aws.sns.calls.clear()
        self.fn['NotifierFn'].handler(stream_event([(device(status=old, **kw), device(status=new, **kw))]), None)
        return self.publishes()

    def test_owner_gets_one_email_per_change(self):
        p = self.change('SCHEDULED', 'COLLECTED')
        owner = [x for x in p if 'userId' in x['MessageAttributes']]
        self.assertEqual(len(owner), 1)
        self.assertEqual(owner[0]['MessageAttributes']['userId']['StringValue'], 'u1')
        self.assertIn('Collected', owner[0]['Message'])

    def test_staff_are_told_when_work_arrives_in_their_queue(self):
        cases = [('PICKUP_REQUESTED', 'SCHEDULED', {}, 'collectors'), ('IN_REPAIR', 'REPAIRED', {}, 'collectors'),
                 ('IN_REPAIR', 'UNREPAIRABLE', {}, 'processors'), ('SCHEDULED', 'COLLECTED', {'category': 'REPAIR'}, 'repairers'),
                 ('SCHEDULED', 'COLLECTED', {'category': 'REUSE'}, 'processors'), ('SCHEDULED', 'COLLECTED', {'category': 'RECYCLE'}, 'processors')]
        for old, new, kw, role in cases:
            staff = [x for x in self.change(old, new, **kw) if 'staffRole' in x['MessageAttributes']]
            self.assertEqual([x['MessageAttributes']['staffRole']['StringValue'] for x in staff], [role], (old, new))

    def test_staff_messages_carry_no_address_or_owner(self):
        staff = [x for x in self.change('PICKUP_REQUESTED', 'SCHEDULED', address='221B Baker St', slot='2026-10-21 09:00-12:00') if 'staffRole' in x['MessageAttributes']][0]
        self.assertNotIn('Baker', staff['Message'])
        self.assertNotIn('u1', staff['Message'])
        self.assertIn("'Laptop'", staff['Message'])

    def test_other_changes_notify_staff_nobody(self):
        for old, new in (('COLLECTED', 'IN_REPAIR'), ('PROCESSING', 'REUSED'), ('LISTED', 'RESERVED'), ('SCHEDULED', 'PICKUP_FAILED')):
            self.assertEqual([x for x in self.change(old, new, lastNote='x') if 'staffRole' in x['MessageAttributes']], [], (old, new))

    def test_failed_pickup_email_includes_the_reason(self):
        p = self.change('SCHEDULED', 'PICKUP_FAILED', lastNote='nobody home')
        self.assertIn('nobody home', p[0]['Message'])
        self.assertIn('another slot', p[0]['Message'])

    def test_a_reschedule_emails_the_new_slot(self):
        self.aws.sns.calls.clear()
        old, new = device(status='SCHEDULED', slot='2026-10-21 09:00-12:00'), device(status='SCHEDULED', slot='2026-10-22 12:00-15:00')
        self.fn['NotifierFn'].handler(stream_event([(old, new)]), None)
        p = self.publishes()
        self.assertEqual(len(p), 1)
        self.assertIn('2026-10-22 12:00-15:00', p[0]['Message'])

    def test_quiet_events_send_nothing(self):
        self.aws.sns.calls.clear()
        same = device(status='SCHEDULED', slot='2026-10-21 09:00-12:00')
        self.fn['NotifierFn'].handler(stream_event([(None, device()), (same, dict(same, name='renamed'))]), None)
        self.assertEqual(self.publishes(), [])

    def test_every_status_has_friendly_wording(self):
        for s in STATUSES[1:]:
            msg = [x for x in self.change('REGISTERED', s, lastNote='n') if 'userId' in x['MessageAttributes']][0]['Message']
            self.assertTrue(len(msg) > 40 and ". " in msg or msg.rstrip().endswith('.'), s)


class Subscribe(Base):
    def test_customers_get_their_own_updates(self):
        self.assertEqual(self.call('SubscribeFn', 'POST /subscribe', claims('u9', email='nine@x.com'))['statusCode'], 200)
        kw = [c for c in self.aws.sns.calls if c[0] == 'subscribe'][0][1]
        self.assertEqual((kw['Protocol'], kw['Endpoint']), ('email', 'nine@x.com'))
        import json
        self.assertEqual(json.loads(kw['Attributes']['FilterPolicy']), {'userId': ['u9']})

    def test_staff_get_new_task_alerts_for_their_role_only(self):
        import json
        for role in ('collectors', 'repairers', 'processors'):
            self.aws.sns.calls.clear()
            self.call('SubscribeFn', 'POST /subscribe', claims('s1', [role], 's@x.com'))
            kw = [c for c in self.aws.sns.calls if c[0] == 'subscribe'][0][1]
            self.assertEqual(json.loads(kw['Attributes']['FilterPolicy']), {'staffRole': [role]})

    def test_an_existing_subscription_is_not_a_server_error(self):
        self.aws.sns.fail_subscribe = True
        r = self.call('SubscribeFn', 'POST /subscribe', claims('u9'))
        self.assertEqual(r['statusCode'], 200)
        self.assertIn('already subscribed', body(r)['message'])
