import json, os, re, time, boto3
from botocore.exceptions import ClientError
ddb = boto3.resource('dynamodb')
D, S = ddb.Table(os.environ['DEVICES']), ddb.Table(os.environ['SLOTS'])
ST = ('collectors', 'repairers', 'processors')
TR = {('SCHEDULED', 'COLLECTED'): 'collectors', ('SCHEDULED', 'PICKUP_FAILED'): 'collectors',
      ('COLLECTED', 'IN_REPAIR'): 'repairers', ('COLLECTED', 'PROCESSING'): 'processors',
      ('IN_REPAIR', 'REPAIRED'): 'repairers', ('IN_REPAIR', 'UNREPAIRABLE'): 'repairers',
      ('UNREPAIRABLE', 'PROCESSING'): 'processors', ('REPAIRED', 'RETURNED'): 'collectors',
      ('PROCESSING', 'REUSED'): 'processors', ('PROCESSING', 'RECYCLED'): 'processors',
      ('PROCESSING', 'LISTED'): 'processors', ('LISTED', 'PROCESSING'): 'processors',
      ('RESERVED', 'REUSED'): 'processors', ('RESERVED', 'LISTED'): 'processors'}
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid = cl['sub']
    role = next((r for r in ST if r in re.findall(r'[a-z]+', str(cl.get('cognito:groups', '')))), 'user')
    try:
        body = json.loads(e.get('body') or '{}')
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get('status'), str):
        return resp(400, {'error': 'a status is required'})
    did = e['pathParameters']['id']
    d = D.get_item(Key={'deviceId': did}).get('Item')
    if not d:
        return resp(404, {'error': 'device not found'})
    frm, new, cat = d['status'], body['status'], d['category']
    need = TR.get((frm, new))
    if not need:
        return resp(409, {'error': 'that status change is not allowed'})
    if role != need:
        return resp(403, {'error': 'only ' + need + ' can do this step'})
    if (new == 'IN_REPAIR' and cat != 'REPAIR') or (frm == 'COLLECTED' and new == 'PROCESSING' and cat == 'REPAIR') or (new == 'LISTED' and frm == 'PROCESSING' and cat == 'RECYCLE'):
        return resp(409, {'error': 'not allowed for a device with the ' + cat.lower() + ' plan'})
    note = body.get('note')
    note = note.strip()[:100] if isinstance(note, str) else ''
    now = int(time.time())
    sets, rem = {}, []
    if new == 'PICKUP_FAILED':
        if not note:
            return resp(400, {'error': 'a reason is required'})
        sets, rem = {'failedAttempts': int(d.get('failedAttempts', 0)) + 1, 'lastNote': note}, ['slot']
    if new in ('RETURNED', 'REUSED', 'RECYCLED'):
        sets['expiresAt'] = now + 31536000
    if new == 'LISTED':
        sets['listedAt'] = now
        rem = ['reservedBy'] if frm == 'RESERVED' else []
    h = {'s': new, 'f': frm, 't': now, 'by': uid, 'r': role}
    if note:
        h['n'] = note
    ex = 'SET #s=:s, history=list_append(if_not_exists(history,:e),:h)' + ''.join(', %s=:%s' % (k, k) for k in sets) + (' REMOVE ' + ', '.join(rem) if rem else '')
    v = dict({':s': new, ':f': frm, ':e': [], ':h': [h]}, **{':' + k: x for k, x in sets.items()})
    try:
        D.update_item(Key={'deviceId': did}, UpdateExpression=ex, ConditionExpression='#s = :f', ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues=v)
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return resp(409, {'error': 'the device changed, please refresh'})
        raise
    if new == 'PICKUP_FAILED' and d.get('slot'):
        try:
            S.update_item(Key={'slotId': d['slot']}, UpdateExpression='ADD booked :m', ConditionExpression='booked > :z', ExpressionAttributeValues={':m': -1, ':z': 0})
        except ClientError:
            pass
    return resp(200, {'message': 'updated'})
