import json, os, re, time, datetime as dt, boto3
from botocore.exceptions import ClientError
ddb = boto3.resource('dynamodb')
D, S = ddb.Table(os.environ['DEVICES']), ddb.Table(os.environ['SLOTS'])
CAP, CUT = int(os.environ['CAP']), int(os.environ['CUTOFF'])
WIN = ('09:00-12:00', '12:00-15:00', '15:00-18:00')
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def slots():
    d0 = dt.datetime.now(dt.timezone.utc).date()
    return [str(d0 + dt.timedelta(days=n)) + ' ' + w for n in range(2, 9) for w in WIN]
def code(ex):
    return ex.response['Error']['Code']
def take(s):
    try:
        S.update_item(Key={'slotId': s}, UpdateExpression='ADD booked :one SET expiresAt=:x', ConditionExpression='attribute_not_exists(booked) OR booked < :cap',
                      ExpressionAttributeValues={':one': 1, ':cap': CAP, ':x': int(time.time()) + 1296000})
        return True
    except ClientError as ex:
        if code(ex) != 'ConditionalCheckFailedException':
            raise
        return False
def free(s):
    try:
        S.update_item(Key={'slotId': s}, UpdateExpression='ADD booked :m', ConditionExpression='booked > :z', ExpressionAttributeValues={':m': -1, ':z': 0})
    except ClientError:
        pass
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid, rk = cl['sub'], e['routeKey']
    if re.search(r'collectors|repairers|processors', str(cl.get('cognito:groups', ''))):
        return resp(403, {'error': 'customers only'})
    try:
        body = json.loads(e.get('body') or '{}')
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return resp(400, {'error': 'invalid JSON body'})
    did = e['pathParameters']['id']
    d = D.get_item(Key={'deviceId': did}).get('Item')
    if not d or d['userId'] != uid:
        return resp(404, {'error': 'device not found'})
    if d['status'] != 'SCHEDULED':
        return resp(409, {'error': 'only a scheduled pickup can be changed'})
    if dt.datetime.strptime(d['slot'][:16], '%Y-%m-%d %H:%M') - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) < dt.timedelta(hours=CUT):
        return resp(409, {'error': 'too late to change this pickup (cutoff %d hours)' % CUT})
    new = body.get('slot')
    resched = rk == 'POST /devices/{id}/reschedule'
    if resched and (new not in slots() or new == d['slot']):
        return resp(400, {'error': 'choose one of the available slots'})
    if resched and not take(new):
        return resp(409, {'error': 'that slot is full'})
    h = {'s': 'SCHEDULED' if resched else 'REGISTERED', 'f': 'SCHEDULED', 't': int(time.time()), 'by': uid, 'r': 'user', 'n': 'rescheduled' if resched else 'cancelled by customer'}
    try:
        D.update_item(Key={'deviceId': did}, UpdateExpression='SET #s=:s, history=list_append(if_not_exists(history,:e),:h)' + (', slot=:t' if resched else ' REMOVE slot'),
                      ConditionExpression='#s = :f AND slot = :o', ExpressionAttributeNames={'#s': 'status'},
                      ExpressionAttributeValues=dict({':s': h['s'], ':f': 'SCHEDULED', ':o': d['slot'], ':e': [], ':h': [h]}, **({':t': new} if resched else {})))
    except ClientError as ex:
        if resched:
            free(new)
        if code(ex) == 'ConditionalCheckFailedException':
            return resp(409, {'error': 'the pickup changed, please refresh'})
        raise
    free(d['slot'])
    return resp(200, {'slot': new} if resched else {'message': 'pickup cancelled'})
