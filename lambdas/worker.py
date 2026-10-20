import json, os, time, datetime as dt, boto3
from botocore.exceptions import ClientError
ddb = boto3.resource('dynamodb')
D, R, S = ddb.Table(os.environ['DEVICES']), ddb.Table(os.environ['REQUESTS']), ddb.Table(os.environ['SLOTS'])
CAP = int(os.environ['CAP'])
WIN = ('09:00-12:00', '12:00-15:00', '15:00-18:00')
def slots():
    d0 = dt.datetime.now(dt.timezone.utc).date()
    return [str(d0 + dt.timedelta(days=n)) + ' ' + w for n in range(2, 9) for w in WIN]
def take(s):
    try:
        S.update_item(Key={'slotId': s}, UpdateExpression='ADD booked :one SET expiresAt=:x', ConditionExpression='attribute_not_exists(booked) OR booked < :cap',
                      ExpressionAttributeValues={':one': 1, ':cap': CAP, ':x': int(time.time()) + 1296000})
        return True
    except ClientError as ex:
        if ex.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        return False
def handler(e, ctx):
    for r in e['Records']:
        did = json.loads(r['body'])['deviceId']
        d = D.get_item(Key={'deviceId': did}).get('Item')
        if not d or d['status'] != 'PICKUP_REQUESTED':
            continue                                    # duplicate or outdated message
        slot = next((s for s in slots() if take(s)), None)
        if not slot:
            raise RuntimeError('no free pickup slot')   # SQS retries, then the dead-letter queue and an alarm
        h = {'s': 'SCHEDULED', 'f': 'PICKUP_REQUESTED', 't': int(time.time()), 'by': 'system', 'r': 'system'}
        try:
            D.update_item(Key={'deviceId': did}, UpdateExpression='SET #s=:s, slot=:t, history=list_append(if_not_exists(history,:e),:h)',
                          ConditionExpression='#s = :f', ExpressionAttributeNames={'#s': 'status'},
                          ExpressionAttributeValues={':s': 'SCHEDULED', ':f': 'PICKUP_REQUESTED', ':t': slot, ':e': [], ':h': [h]})
        except ClientError:
            S.update_item(Key={'slotId': slot}, UpdateExpression='ADD booked :m', ExpressionAttributeValues={':m': -1})
            continue
        R.update_item(Key={'requestId': did}, UpdateExpression='SET #s=:s, slot=:t', ExpressionAttributeNames={'#s': 'status'},
                      ExpressionAttributeValues={':s': 'SCHEDULED', ':t': slot})
