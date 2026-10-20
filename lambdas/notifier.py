import os, boto3
sns = boto3.client('sns')
TXT = {'PICKUP_REQUESTED': 'We received your pickup request.', 'SCHEDULED': 'Pickup scheduled.',
       'PICKUP_FAILED': 'We could not collect it. You can request another slot.',
       'COLLECTED': 'Our collector picked it up.', 'IN_REPAIR': 'It is now being repaired.',
       'REPAIRED': 'It has been repaired and will be returned to you.',
       'UNREPAIRABLE': 'It could not be repaired and will go to recycling or reuse.',
       'PROCESSING': 'It reached our processing partner.', 'LISTED': 'It is now listed on the reuse marketplace.',
       'RESERVED': 'Someone reserved it from the marketplace.', 'REUSED': 'It was refurbished and given a second life.',
       'RECYCLED': 'It was recycled responsibly.', 'RETURNED': 'It has been returned to you.'}
STAFF = {'SCHEDULED': 'collectors', 'REPAIRED': 'collectors', 'UNREPAIRABLE': 'processors'}
def pub(subject, msg, attr, val):
    sns.publish(TopicArn=os.environ['TOPIC'], Subject=subject, Message=msg, MessageAttributes={attr: {'DataType': 'String', 'StringValue': val}})
def handler(e, ctx):
    for r in e['Records']:
        if r['eventName'] != 'MODIFY':
            continue
        n, o = r['dynamodb']['NewImage'], r['dynamodb']['OldImage']
        g = lambda i, k: i.get(k, {}).get('S', '')
        st, name, slot = g(n, 'status'), g(n, 'name'), g(n, 'slot')
        if st != g(o, 'status'):
            msg = "ReCircuit update for '%s': %s. %s" % (name, st.replace('_', ' ').title(), TXT.get(st, ''))
            if st == 'SCHEDULED' and slot:
                msg += ' Slot: ' + slot + '.'
            if st == 'PICKUP_FAILED':
                msg += ' Reason: ' + g(n, 'lastNote') + '.'
            pub('ReCircuit status update', msg, 'userId', n['userId']['S'])
            to = ('repairers' if g(n, 'category') == 'REPAIR' else 'processors') if st == 'COLLECTED' else STAFF.get(st)
            if to:
                pub('ReCircuit new task', "ReCircuit task: '%s' is waiting for you (%s)." % (name, st.replace('_', ' ').title()), 'staffRole', to)
        elif slot and slot != g(o, 'slot'):
            pub('ReCircuit pickup rescheduled', "ReCircuit: the pickup for '%s' moved to %s." % (name, slot), 'userId', n['userId']['S'])
