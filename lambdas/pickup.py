import json, os, re, time, datetime as dt, boto3
from botocore.exceptions import ClientError
ddb = boto3.resource('dynamodb')
D, R, S = ddb.Table(os.environ['DEVICES']), ddb.Table(os.environ['REQUESTS']), ddb.Table(os.environ['SLOTS'])
sqs = boto3.client('sqs')
CAP = int(os.environ['CAP'])
WIN = ('09:00-12:00', '12:00-15:00', '15:00-18:00')
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def slots():
    d0 = dt.datetime.now(dt.timezone.utc).date()
    return [str(d0 + dt.timedelta(days=n)) + ' ' + w for n in range(2, 9) for w in WIN]
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid = cl['sub']
    if re.search(r'collectors|repairers|processors', str(cl.get('cognito:groups', ''))):
        return resp(403, {'error': 'customers only'})
    if e['routeKey'] == 'GET /slots':
        booked = {i['slotId']: int(i.get('booked', 0)) for i in S.scan()['Items']}
        return resp(200, {'slots': [{'slot': s, 'left': CAP - booked.get(s, 0)} for s in slots() if booked.get(s, 0) < CAP]})
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
    st, now = d['status'], int(time.time())
    if st not in ('REGISTERED', 'PICKUP_FAILED'):
        return resp(409, {'error': 'pickup already requested'})
    if int(d.get('failedAttempts', 0)) >= 3:
        return resp(409, {'error': 'too many failed pickups, please contact support'})
    addr = str(body.get('address') or d.get('address') or '').strip()[:200]
    if not addr:
        return resp(400, {'error': 'address is required'})
    h = {'s': 'PICKUP_REQUESTED', 'f': st, 't': now, 'by': uid, 'r': 'user'}
    try:
        D.update_item(Key={'deviceId': did}, UpdateExpression='SET #s=:s, address=:a, history=list_append(if_not_exists(history,:e),:h) REMOVE slot',
                      ConditionExpression='#s = :f', ExpressionAttributeNames={'#s': 'status'},
                      ExpressionAttributeValues={':s': 'PICKUP_REQUESTED', ':a': addr, ':f': st, ':e': [], ':h': [h]})
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return resp(409, {'error': 'the device changed, please refresh'})
        raise
    R.put_item(Item={'requestId': did, 'deviceId': did, 'userId': uid, 'address': addr, 'status': 'PENDING', 'createdAt': now, 'expiresAt': now + 31536000})
    sqs.send_message(QueueUrl=os.environ['QUEUE'], MessageBody=json.dumps({'requestId': did, 'deviceId': did}))
    return resp(202, {'message': 'pickup requested'})
