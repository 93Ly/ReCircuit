"""Test harness: loads the Lambda code embedded in recircuit-template.yaml and runs it against in-memory fakes
of DynamoDB (including conditional writes), S3, SQS and SNS. No AWS account or network needed."""
import copy
import json
import os
import re
import sys
import types
from decimal import Decimal

TEMPLATE = os.path.join(os.path.dirname(__file__), '..', '..', 'recircuit-template.yaml')


def lambda_sources():
    """Return {resource_name: python_source} for every inline (ZipFile) Lambda in the template."""
    with open(TEMPLATE, encoding='utf-8') as f:
        lines = f.read().split('\n')
    out, res, i = {}, None, 0
    while i < len(lines):
        m = re.match(r'^  (\w+):$', lines[i])
        if m:
            res = m.group(1)
        if lines[i].strip() == 'ZipFile: |':
            code = []
            i += 1
            while i < len(lines) and (lines[i].startswith(' ' * 10) or not lines[i].strip()):
                code.append(lines[i][10:])
                i += 1
            out[res] = '\n'.join(code).rstrip() + '\n'
            continue
        i += 1
    return out


# ---------------------------------------------------------------- fakes
class ClientError(Exception):
    def __init__(self, code, op='Operation'):
        super().__init__(f'{code} when calling {op}')
        self.response = {'Error': {'Code': code}}


class Cond:
    def __init__(self, attr, value):
        self.attr, self.value = attr, value


class Key:
    def __init__(self, name):
        self.name = name

    def eq(self, value):
        return Cond(self.name, value)


def _num(x):
    return Decimal(str(x)) if isinstance(x, (int, float)) else x


class FakeTable:
    def __init__(self, key, stream):
        self.key, self.items, self.stream = key, {}, stream

    def _record(self, old, new):
        self.stream.append((copy.deepcopy(old), copy.deepcopy(new)))

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {'Item': copy.deepcopy(item)} if item else {}

    def put_item(self, Item):
        old = self.items.get(Item[self.key])
        self.items[Item[self.key]] = copy.deepcopy(Item)
        self._record(old, Item)

    # --- condition expressions: "attribute_not_exists(x) OR x < :v", "#s = :f AND slot = :o", "x > :z"
    def _check(self, expr, item, names, vals):
        def ref(tok):
            tok = tok.strip()
            if tok.startswith(':'):
                return vals[tok]
            return item.get(names.get(tok, tok))

        def atom(a):
            a = a.strip()
            m = re.fullmatch(r'attribute_not_exists\((\S+)\)', a)
            if m:
                return names.get(m.group(1), m.group(1)) not in item
            m = re.fullmatch(r'(\S+)\s*(<=|>=|=|<|>)\s*(\S+)', a)
            lhs, op, rhs = ref(m.group(1)), m.group(2), ref(m.group(3))
            if lhs is None:
                return False
            lhs, rhs = _num(lhs), _num(rhs)
            return {'=': lhs == rhs, '<': lhs < rhs, '>': lhs > rhs, '<=': lhs <= rhs, '>=': lhs >= rhs}[op]
        return any(all(atom(a) for a in part.split(' AND ')) for part in expr.split(' OR '))

    def update_item(self, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        names, vals = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        current = self.items.get(Key[self.key])
        if ConditionExpression and not self._check(ConditionExpression, current or {}, names, vals):
            raise ClientError('ConditionalCheckFailedException', 'UpdateItem')
        item = copy.deepcopy(current) if current else {self.key: Key[self.key]}
        old = copy.deepcopy(current)
        sections = re.split(r'\b(SET|REMOVE|ADD)\b', UpdateExpression)
        for kw, body in zip(sections[1::2], sections[2::2]):
            body = body.strip()
            if kw == 'REMOVE':
                for a in body.split(','):
                    item.pop(names.get(a.strip(), a.strip()), None)
            elif kw == 'ADD':
                a, v = body.split()
                a = names.get(a, a)
                item[a] = item.get(a, 0) + vals[v]
            else:
                parts, depth, cur = [], 0, ''
                for ch in body:
                    depth += ch == '('
                    depth -= ch == ')'
                    if ch == ',' and depth == 0:
                        parts.append(cur); cur = ''
                    else:
                        cur += ch
                parts.append(cur)
                for p in parts:
                    lhs, rhs = [x.strip() for x in p.split('=', 1)]
                    lhs = names.get(lhs, lhs)
                    m = re.fullmatch(r'list_append\(if_not_exists\((\w+),(:\w+)\),(:\w+)\)', rhs)
                    if m:
                        item[lhs] = copy.deepcopy(item.get(m.group(1), vals[m.group(2)])) + copy.deepcopy(vals[m.group(3)])
                    else:
                        item[lhs] = copy.deepcopy(vals[rhs])
        self.items[Key[self.key]] = item
        self._record(old, item)

    def query(self, IndexName=None, KeyConditionExpression=None):
        c = KeyConditionExpression
        return {'Items': [copy.deepcopy(i) for i in self.items.values() if i.get(c.attr) == c.value]}

    def scan(self):
        return {'Items': [copy.deepcopy(i) for i in self.items.values()]}


class Recorder:
    def __init__(self):
        self.calls, self.fail_subscribe = [], False

    def generate_presigned_url(self, op, Params=None, ExpiresIn=0):
        return 'https://s3.example/%s/%s?exp=%s&ct=%s' % (op, Params['Key'], ExpiresIn, Params.get('ContentType', ''))

    def send_message(self, **kw):
        self.calls.append(('send_message', kw))

    def subscribe(self, **kw):
        if self.fail_subscribe:
            raise RuntimeError('InvalidParameter: Subscription already exists with different attributes')
        self.calls.append(('subscribe', kw))

    def publish(self, **kw):
        self.calls.append(('publish', kw))


class AWS:
    """A fresh fake AWS world, installed as `boto3` for the Lambda modules."""

    def __init__(self, cap=3, cutoff=24):
        self.cap, self.cutoff = cap, cutoff
        self.stream = []  # (old_item, new_item) pairs for the devices table
        self.tables = {'Devices': FakeTable('deviceId', self.stream), 'Requests': FakeTable('requestId', []), 'Slots': FakeTable('slotId', [])}
        self.s3, self.sqs, self.sns = Recorder(), Recorder(), Recorder()

    def install(self):
        aws = self
        boto3 = types.ModuleType('boto3')
        boto3.resource = lambda svc: types.SimpleNamespace(Table=lambda name: aws.tables[name])
        boto3.client = lambda svc, **kw: {'s3': aws.s3, 'sqs': aws.sqs, 'sns': aws.sns}[svc]
        dynamodb = types.ModuleType('boto3.dynamodb')
        conditions = types.ModuleType('boto3.dynamodb.conditions')
        conditions.Key = Key
        botocore = types.ModuleType('botocore')
        bconfig = types.ModuleType('botocore.config')
        bconfig.Config = lambda **kw: None
        bexc = types.ModuleType('botocore.exceptions')
        bexc.ClientError = ClientError
        sys.modules.update({'boto3': boto3, 'boto3.dynamodb': dynamodb, 'boto3.dynamodb.conditions': conditions,
                            'botocore': botocore, 'botocore.config': bconfig, 'botocore.exceptions': bexc})
        os.environ.update(DEVICES='Devices', REQUESTS='Requests', SLOTS='Slots', BUCKET='bucket', QUEUE='queue-url',
                          TOPIC='topic-arn', CAP=str(self.cap), CUTOFF=str(self.cutoff))

    def load(self, resource):
        self.install()
        mod = types.ModuleType(resource)
        exec(compile(lambda_sources()[resource], resource, 'exec'), mod.__dict__)
        return mod


# ---------------------------------------------------------------- event helpers
def claims(sub='u1', groups=None, email=None):
    c = {'sub': sub, 'email': email or sub + '@example.com'}
    if groups:
        c['cognito:groups'] = '[' + ' '.join(groups) + ']'  # API Gateway's string form of a JWT array claim
    return c


def api_event(route, cl, body=None, device_id=None, query=None, raw_body=None):
    e = {'routeKey': route, 'requestContext': {'authorizer': {'jwt': {'claims': cl}}},
         'body': raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)}
    if device_id:
        e['pathParameters'] = {'id': device_id}
    if query:
        e['queryStringParameters'] = query
    return e


def body(resp):
    return json.loads(resp['body'])


def device(**kw):
    d = {'deviceId': 'd1', 'userId': 'u1', 'name': 'Laptop', 'type': 'laptop', 'condition': 'working', 'age': 3,
         'category': 'REUSE', 'status': 'REGISTERED', 'photoKey': '', 'createdAt': 1,
         'history': [{'s': 'REGISTERED', 't': 1, 'by': 'u1', 'r': 'user'}]}
    d.update(kw)
    return d


def stream_event(pairs):
    """Convert (old, new) item pairs into a DynamoDB Streams event for the notifier Lambda."""
    img = lambda it: {k: {'S': v} for k, v in it.items() if isinstance(v, str)}
    return {'Records': [{'eventName': 'MODIFY' if old else 'INSERT',
                         'dynamodb': {'NewImage': img(new), 'OldImage': img(old) if old else {}}} for old, new in pairs]}
