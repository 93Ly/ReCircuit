import json, os, re, uuid, time, boto3
from botocore.config import Config
T = boto3.resource('dynamodb').Table(os.environ['DEVICES'])
s3 = boto3.client('s3', config=Config(signature_version='s3v4'))
CATS = ('REUSE', 'REPAIR', 'RECYCLE')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
TYPES = ('laptop', 'phone', 'tablet', 'battery', 'appliance', 'other')
RULES = {'working': (8, 'REUSE', 'Working and at most 8 years old: suitable for refurbishment and reuse'),
         'needs repair': (6, 'REPAIR', 'Repairable and at most 6 years old: worth repairing'),
         'broken': (3, 'REPAIR', 'Broken but at most 3 years old: assess it for repair')}
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b, default=str)}
def triage(t, c, age):
    if t == 'battery':
        return 'RECYCLE', 'Batteries are hazardous and are never repaired or reused'
    lim, rec, why = RULES[c]
    return (rec, why) if age <= lim else ('RECYCLE', 'Older than %d years for this condition: recycle to recover the materials' % lim)
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    uid = cl['sub']
    if re.search(r'collectors|repairers|processors', str(cl.get('cognito:groups', ''))):
        return resp(403, {'error': 'customers only'})
    try:
        body = json.loads(e.get('body') or '{}')
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return resp(400, {'error': 'invalid JSON body'})
    rk = e['routeKey']
    if rk == 'POST /upload-url':
        key = str(uuid.uuid4())   # random: the key never contains the owner id
        ct = 'image/png' if body.get('contentType') == 'image/png' else 'image/jpeg'
        url = s3.generate_presigned_url('put_object', Params={'Bucket': os.environ['BUCKET'], 'Key': key, 'ContentType': ct}, ExpiresIn=300)
        return resp(200, {'uploadUrl': url, 'photoKey': key, 'contentType': ct})
    t, c, age = body.get('type'), body.get('condition'), body.get('age')
    if t not in TYPES or c not in RULES or not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 40:
        return resp(400, {'error': 'type, condition and age (0-40) are required'})
    rec, why = triage(t, c, age)
    a = {'recommended': rec, 'reason': why}
    if rk == 'POST /assess':
        return resp(200, a)
    cat, name, key = body.get('category', rec), body.get('name'), body.get('photoKey', '')
    if cat not in CATS or not isinstance(name, str) or not name.strip() or not isinstance(key, str) or (key and not re.fullmatch(UUID, key)):
        return resp(400, {'error': 'a name, a valid category and a photo uploaded through the app are required'})
    now = int(time.time())
    item = {'deviceId': str(uuid.uuid4()), 'userId': uid, 'name': name.strip()[:100], 'type': t, 'condition': c, 'age': age,
            'category': cat, 'assessment': a, 'status': 'REGISTERED', 'photoKey': key, 'createdAt': now,
            'history': [{'s': 'REGISTERED', 't': now, 'by': uid, 'r': 'user'}]}
    T.put_item(Item=item)
    return resp(201, {'deviceId': item['deviceId'], 'assessment': a})
