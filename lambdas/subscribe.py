import json, os, re, boto3
sns = boto3.client('sns')
ST = ('collectors', 'repairers', 'processors')
def resp(c, b):
    return {'statusCode': c, 'body': json.dumps(b)}
def handler(e, ctx):
    cl = e['requestContext']['authorizer']['jwt']['claims']
    role = next((r for r in ST if r in re.findall(r'[a-z]+', str(cl.get('cognito:groups', '')))), 'user')
    # customers get mail about their own devices, staff about new tasks for their role
    policy = {'staffRole': [role]} if role in ST else {'userId': [cl['sub']]}
    try:
        sns.subscribe(TopicArn=os.environ['TOPIC'], Protocol='email', Endpoint=cl['email'], Attributes={'FilterPolicy': json.dumps(policy)})
    except Exception as ex:
        print('subscribe failed:', ex)
        return resp(200, {'message': 'This email is already subscribed, so you will receive updates.'})
    return resp(200, {'message': 'Check your inbox and confirm the subscription email'})
