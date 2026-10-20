import json, os, re, boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
T = boto3.resource('dynamodb').Table(os.environ['DEVICES'])
s3 = boto3.client('s3', config=Config(signature_version='s3v4'))
SEE = {'collectors': ('SCHEDULED', 'PICKUP_FAILED', 'COLLECTED', 'REPAIRED', 'RETURNED'),
       'repairers': ('COLLECTED', 'IN_REPAIR', 'REPAIRED', 'UNREPAIRABLE'),
       'processors': ('COLLECTED', 'UNREPAIRABLE', 'PROCESSING', 'LISTED', 'RESERVED', 'REUSED', 'RECYCLED')}
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def visible(role, d):
    if d['status'] not in SEE[role]:
        return False
    if d['status'] == 'COLLECTED' and role != 'collectors':
        return (d['category'] == 'REPAIR') == (role == 'repairers')
    return True
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid = cl['sub']
    role = next((r for r in SEE if r in re.findall(r'[a-z]+', str(cl.get('cognito:groups', '')))), 'user')
    if role == 'user':
        items = T.query(IndexName='userId-index', KeyConditionExpression=Key('userId').eq(uid))['Items']
    else:
        items = [d for d in T.scan()['Items'] if visible(role, d)]
    for i in items:
        if i.get('photoKey'):
            i['photoUrl'] = s3.generate_presigned_url('get_object', Params={'Bucket': os.environ['BUCKET'], 'Key': i['photoKey']}, ExpiresIn=300)
        i['history'] = [{k: v for k, v in h.items() if k != 'by'} for h in i.get('history', [])]
        for k in ('userId', 'reservedBy', 'expiresAt', 'photoKey') + (() if role in ('user', 'collectors') else ('address',)):
            i.pop(k, None)
    return resp(200, {'items': items, 'role': role})
