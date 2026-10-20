"""Checks on the CloudFormation template itself: inline-code limits, least-privilege roles, free-tier cost guards."""
import re
import unittest

import yaml

from base import FUNCS  # noqa: F401  (also puts harness on the path)
from harness import TEMPLATE, lambda_sources


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node)}
    return {suffix: loader.construct_mapping(node)}


_Loader.add_multi_constructor('!', _tag)


def load():
    with open(TEMPLATE, encoding='utf-8') as f:
        return yaml.load(f.read(), Loader=_Loader)


# The permission spec: exactly these IAM actions per function, nothing more.
ALLOWED = {
    'RegisterFn': {'dynamodb:PutItem', 's3:PutObject'},
    'ListFn': {'dynamodb:Query', 'dynamodb:Scan', 's3:GetObject'},
    'PickupFn': {'dynamodb:GetItem', 'dynamodb:UpdateItem', 'dynamodb:PutItem', 'dynamodb:Scan', 'sqs:SendMessage'},
    'ChangeFn': {'dynamodb:GetItem', 'dynamodb:UpdateItem'},
    'StatusFn': {'dynamodb:GetItem', 'dynamodb:UpdateItem'},
    'SubscribeFn': {'sns:Subscribe'},
    'MarketFn': {'dynamodb:Scan', 'dynamodb:GetItem', 'dynamodb:UpdateItem', 's3:GetObject'},
    'WorkerFn': {'dynamodb:GetItem', 'dynamodb:UpdateItem', 'sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueAttributes'},
    'NotifierFn': {'dynamodb:DescribeStream', 'dynamodb:GetRecords', 'dynamodb:GetShardIterator', 'dynamodb:ListStreams', 'sns:Publish'},
}
ALLOWED_TYPES = {
    'AWS::S3::Bucket', 'AWS::S3::BucketPolicy', 'AWS::CloudFront::OriginAccessControl', 'AWS::CloudFront::Distribution',
    'AWS::DynamoDB::Table', 'AWS::SNS::Topic', 'AWS::SNS::Subscription', 'AWS::SNS::TopicPolicy', 'AWS::SQS::Queue',
    'AWS::IAM::Role', 'AWS::Lambda::Function', 'AWS::Lambda::EventSourceMapping', 'AWS::Lambda::Permission',
    'AWS::Cognito::UserPool', 'AWS::Cognito::UserPoolClient', 'AWS::Cognito::UserPoolGroup',
    'AWS::ApiGatewayV2::Api', 'AWS::ApiGatewayV2::Authorizer', 'AWS::ApiGatewayV2::Integration',
    'AWS::ApiGatewayV2::Route', 'AWS::ApiGatewayV2::Stage', 'AWS::CloudWatch::Alarm'}


def role_actions(tpl, fn):
    role = tpl['Resources'][fn]['Properties']['Role']['GetAtt'].split('.')[0]
    acts, resources = set(), []
    for pol in tpl['Resources'][role]['Properties']['Policies']:
        for st in pol['PolicyDocument']['Statement']:
            a = st['Action']
            acts |= set(a if isinstance(a, list) else [a])
            resources.append(st['Resource'])
    return role, acts, resources


class InlineCode(unittest.TestCase):
    def test_every_function_fits_the_cloudformation_inline_limit_and_compiles(self):
        srcs = lambda_sources()
        self.assertEqual(set(srcs), set(FUNCS))
        for name, code in srcs.items():
            self.assertLessEqual(len(code), 4096, f'{name} is {len(code)} chars; the CloudFormation inline limit is 4096')
            compile(code, name, 'exec')
            self.assertIn('def handler(', code)

    def test_template_matches_the_lambdas_folder(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        files = {'RegisterFn': 'register.py', 'ListFn': 'devices_list.py', 'PickupFn': 'pickup.py', 'ChangeFn': 'change.py',
                 'StatusFn': 'status.py', 'SubscribeFn': 'subscribe.py', 'MarketFn': 'market.py', 'WorkerFn': 'worker.py', 'NotifierFn': 'notifier.py'}
        srcs = lambda_sources()
        for fn, f in files.items():
            with open(os.path.join(root, 'lambdas', f), encoding='utf-8') as fh:
                self.assertEqual(srcs[fn].rstrip(), fh.read().rstrip(), f'{f} is out of sync; run tools/inject_lambdas.py')

    def test_every_environment_variable_the_code_reads_is_defined(self):
        tpl, srcs = load(), lambda_sources()
        for fn in FUNCS:
            declared = set(tpl['Resources'][fn]['Properties']['Environment']['Variables'])
            used = set(re.findall(r"os\.environ\['(\w+)'\]", srcs[fn]))
            self.assertLessEqual(used, declared, f'{fn} reads {used - declared} which the template does not define')


class LeastPrivilege(unittest.TestCase):
    def test_each_function_has_its_own_role(self):
        tpl = load()
        roles = [role_actions(tpl, fn)[0] for fn in FUNCS]
        self.assertEqual(len(set(roles)), len(FUNCS), 'functions must not share an execution role')

    def test_each_role_has_exactly_the_actions_in_the_spec(self):
        tpl = load()
        for fn in FUNCS:
            _, acts, _ = role_actions(tpl, fn)
            self.assertEqual(acts, ALLOWED[fn], fn)

    def test_no_wildcards_anywhere_in_the_policies(self):
        tpl = load()
        for fn in FUNCS:
            _, acts, resources = role_actions(tpl, fn)
            self.assertFalse(any('*' in a for a in acts), fn)
            for r in resources:
                for item in (r if isinstance(r, list) else [r]):
                    self.assertNotEqual(item, '*', fn)

    def test_read_only_and_write_only_roles(self):
        tpl = load()
        self.assertTrue(all(a.split(':')[1] in ('Query', 'Scan', 'GetObject') for a in role_actions(tpl, 'ListFn')[1]), 'the list function can only read')
        self.assertFalse({'dynamodb:Scan', 'dynamodb:Query', 'dynamodb:UpdateItem'} & role_actions(tpl, 'RegisterFn')[1], 'register can only create')
        self.assertFalse(any(a.startswith('sns:') for a in role_actions(tpl, 'WorkerFn')[1] | role_actions(tpl, 'StatusFn')[1]))

    def test_security_baseline(self):
        t = open(TEMPLATE, encoding='utf-8').read()
        tpl = load()
        self.assertEqual(t.count('PublicAccessBlockConfiguration'), 2, 'both S3 buckets must block public access')
        self.assertIn('SSEAlgorithm: AES256', t)
        routes = [r for r in tpl['Resources'].values() if r['Type'] == 'AWS::ApiGatewayV2::Route']
        self.assertGreaterEqual(len(routes), 13)
        for r in routes:
            self.assertEqual(r['Properties']['AuthorizationType'], 'JWT', r['Properties']['RouteKey'])
        for g in ('collectors', 'repairers', 'processors'):
            self.assertIn(f'GroupName: {g}', t)

    def test_throttling_and_retry_protection(self):
        tpl = load()
        stage = tpl['Resources']['Stage']['Properties']['DefaultRouteSettings']
        self.assertLessEqual(stage['ThrottlingRateLimit'], 20)
        self.assertEqual(tpl['Resources']['PickupQueue']['Properties']['RedrivePolicy']['maxReceiveCount'], 3)


class FreeTierGuards(unittest.TestCase):
    """If any of these fail, the project could start to cost money."""

    def test_only_free_tier_resource_types(self):
        tpl = load()
        bad = {n: r['Type'] for n, r in tpl['Resources'].items() if r['Type'] not in ALLOWED_TYPES}
        self.assertEqual(bad, {}, 'resource types outside the reviewed free-tier list')

    def test_dynamodb_stays_inside_the_always_free_allowance(self):
        tpl = load()
        rcu = wcu = 0
        for name, r in tpl['Resources'].items():
            if r['Type'] != 'AWS::DynamoDB::Table':
                continue
            p = r['Properties']
            self.assertEqual(p['BillingMode'], 'PROVISIONED', name)
            units = [p['ProvisionedThroughput']] + [g['ProvisionedThroughput'] for g in p.get('GlobalSecondaryIndexes', [])]
            rcu += sum(u['ReadCapacityUnits'] for u in units)
            wcu += sum(u['WriteCapacityUnits'] for u in units)
            self.assertTrue(p['TimeToLiveSpecification']['Enabled'], name + ' needs a TTL for data retention')
        self.assertLessEqual(rcu, 25)
        self.assertLessEqual(wcu, 25)

    def test_lambda_settings(self):
        tpl = load()
        for fn in FUNCS:
            p = tpl['Resources'][fn]['Properties']
            self.assertEqual(p['Runtime'], 'python3.12')
            self.assertLessEqual(p['Timeout'], 30)
            self.assertNotIn('ReservedConcurrentExecutions', p)
            self.assertNotIn('MemorySize', p, 'default memory keeps GB-seconds low')

    def test_alarms_are_simple_standard_alarms_within_the_free_ten(self):
        """The free allowance covers standard alarms on a metric directly, not metric-math alarms."""
        tpl = load()
        alarms = [r['Properties'] for r in tpl['Resources'].values() if r['Type'] == 'AWS::CloudWatch::Alarm']
        self.assertLessEqual(len(alarms), 10)
        for a in alarms:
            self.assertIn('MetricName', a)
            self.assertNotIn('Metrics', a, 'metric-math alarms are billed per metric')
            self.assertGreaterEqual(a['Period'], 60, 'high-resolution alarms are not free')

    def test_photo_retention_policy(self):
        tpl = load()
        rule = tpl['Resources']['PhotoBucket']['Properties']['LifecycleConfiguration']['Rules'][0]
        self.assertEqual(rule['ExpirationInDays'], 365)
        self.assertEqual(rule['Transitions'][0]['TransitionInDays'], 90)
        self.assertIn('AbortIncompleteMultipartUpload', rule)

    def test_slot_capacity_and_cutoff_are_parameters(self):
        tpl = load()
        self.assertEqual(tpl['Parameters']['SlotCapacity']['Default'], 3)
        self.assertEqual(tpl['Parameters']['CancelCutoffHours']['Default'], 24)


if __name__ == '__main__':
    unittest.main()
