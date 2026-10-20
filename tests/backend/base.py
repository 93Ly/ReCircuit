import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from harness import AWS, api_event, body, claims, device, stream_event  # noqa: E402,F401

FUNCS = ('RegisterFn', 'ListFn', 'PickupFn', 'ChangeFn', 'StatusFn', 'SubscribeFn', 'MarketFn', 'WorkerFn', 'NotifierFn')
STATUSES = ['REGISTERED', 'PICKUP_REQUESTED', 'SCHEDULED', 'PICKUP_FAILED', 'COLLECTED', 'IN_REPAIR', 'REPAIRED', 'UNREPAIRABLE',
            'RETURNED', 'PROCESSING', 'LISTED', 'RESERVED', 'REUSED', 'RECYCLED']
FINAL = ('RETURNED', 'REUSED', 'RECYCLED')
STAFF = ('collectors', 'repairers', 'processors')
ROLES = {'user': None, 'collectors': ['collectors'], 'repairers': ['repairers'], 'processors': ['processors']}


class Base(unittest.TestCase):
    CAP = 3
    CUTOFF = 24

    def setUp(self):
        self.aws = AWS(cap=self.CAP, cutoff=self.CUTOFF)
        self.fn = {n: self.aws.load(n) for n in FUNCS}
        self.D, self.R, self.S = (self.aws.tables[x] for x in ('Devices', 'Requests', 'Slots'))

    def seed(self, **kw):
        d = device(**kw)
        self.D.items[d['deviceId']] = d
        return d

    def call(self, fn, route, cl, payload=None, did=None, query=None, raw=None):
        return self.fn[fn].handler(api_event(route, cl, payload, did, query, raw), None)

    def staff(self, role, sub=None):
        return claims(sub or role[:3], [role])

    def slots(self):
        return self.fn['PickupFn'].slots()

    def run_worker(self):
        """Deliver every pickup message queued so far to the scheduler Lambda, then clear the queue."""
        msgs = [c[1] for c in self.aws.sqs.calls if c[0] == 'send_message']
        self.aws.sqs.calls = [c for c in self.aws.sqs.calls if c[0] != 'send_message']
        self.fn['WorkerFn'].handler({'Records': [{'body': m['MessageBody']} for m in msgs]}, None)
