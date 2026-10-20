import json, os, re, time, boto3
from botocore.config import Config
from botocore.exceptions import ClientError
D = boto3.resource('dynamodb').Table(os.environ['DEVICES'])
s3 = boto3.client('s3', config=Config(signature_version='s3v4'))
B = os.environ['BUCKET']
FILTERS = {'type': ('laptop', 'phone', 'tablet', 'battery', 'appliance', 'other'),
           'condition': ('working', 'needs repair', 'broken'), 'category': ('REUSE', 'REPAIR', 'RECYCLE')}
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def mv(did, f, t, uid, sets=None, rem=()):
    h = {'s': t, 'f': f, 't': int(time.time()), 'by': uid, 'r': 'user'}
    sets = sets or {}
    ex = 'SET #s=:s, history=list_append(if_not_exists(history,:e),:h)' + ''.join(', %s=:%s' % (k, k) for k in sets)
    if rem:
        ex += ' REMOVE ' + ', '.join(rem)
    v = {':s': t, ':f': f, ':e': [], ':h': [h]}
    v.update({':' + k: x for k, x in sets.items()})
    D.update_item(Key={'deviceId': did}, UpdateExpression=ex, ConditionExpression='#s = :f', ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues=v)
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid, rk = cl['sub'], e['routeKey']
    if rk == 'GET /market':
        q = e.get('queryStringParameters') or {}
        if any(k in q and q[k] not in FILTERS[k] for k in FILTERS):
            return resp(400, {'error': 'unknown filter value'})
        out = []
        for d in D.scan()['Items']:
            mine = d.get('reservedBy') == uid
            if not (d['status'] == 'LISTED' or (mine and d['status'] in ('RESERVED', 'REUSED'))):
                continue
            if any(k in q and d.get(k) != q[k] for k in FILTERS):
                continue
            item = {k: d.get(k) for k in ('deviceId', 'name', 'type', 'condition', 'age', 'category', 'status')}
            item['mine'] = mine
            if d.get('photoKey'):
                item['photoUrl'] = s3.generate_presigned_url('get_object', Params={'Bucket': B, 'Key': d['photoKey']}, ExpiresIn=300)
            out.append(item)
        return resp(200, {'items': out})
    if re.search(r'collectors|repairers|processors', str(cl.get('cognito:groups', ''))):
        return resp(403, {'error': 'customers only'})
    did = e['pathParameters']['id']
    d = D.get_item(Key={'deviceId': did}).get('Item')
    if not d:
        return resp(404, {'error': 'item not found'})
    try:
        if rk == 'POST /market/{id}/reserve':
            if d['userId'] == uid:
                return resp(403, {'error': 'you cannot reserve your own device'})
            if d['status'] != 'LISTED':
                return resp(409, {'error': 'this item is not available'})
            mv(did, 'LISTED', 'RESERVED', uid, {'reservedBy': uid})
        else:
            if d.get('reservedBy') != uid or d['status'] != 'RESERVED':
                return resp(409, {'error': 'you have no reservation on this item'})
            mv(did, 'RESERVED', 'LISTED', uid, rem=('reservedBy',))
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return resp(409, {'error': 'someone else got there first'})
        raise
    return resp(200, {'message': 'ok'})
