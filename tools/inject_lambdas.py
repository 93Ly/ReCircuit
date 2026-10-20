"""Copies the code in lambdas/*.py into the inline (ZipFile) blocks of recircuit-template.yaml.

    tests\\.venv\\Scripts\\python tools\\inject_lambdas.py

lambdas/ is the place to edit function code; the template is what gets deployed and what the tests load.
CloudFormation limits inline code to 4096 characters per function, so this script refuses larger files.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, 'recircuit-template.yaml')
FUNCTIONS = {
    'RegisterFn': 'register.py', 'ListFn': 'devices_list.py', 'PickupFn': 'pickup.py', 'ChangeFn': 'change.py',
    'StatusFn': 'status.py', 'SubscribeFn': 'subscribe.py', 'MarketFn': 'market.py',
    'WorkerFn': 'worker.py', 'NotifierFn': 'notifier.py',
}


def main():
    with open(TEMPLATE, encoding='utf-8') as f:
        lines = f.read().split('\n')
    for res, fname in FUNCTIONS.items():
        with open(os.path.join(ROOT, 'lambdas', fname), encoding='utf-8') as f:
            code = f.read().rstrip('\n')
        if len(code) > 4096:
            sys.exit(f'{fname} is {len(code)} characters; the inline limit is 4096')
        start = lines.index(f'  {res}:')
        z = next(i for i in range(start, len(lines)) if lines[i].strip() == 'ZipFile: |')
        end = z + 1
        while end < len(lines) and (lines[end].startswith(' ' * 10) or not lines[end].strip()):
            end += 1
        while lines[end - 1].strip() == '':
            end -= 1
        lines[z + 1:end] = ['          ' + ln if ln else '' for ln in code.split('\n')]
        print(f'{res:12} <- {fname:16} {len(code):5} chars')
    with open(TEMPLATE, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    main()
