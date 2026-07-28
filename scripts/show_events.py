# pylint: disable=django-not-configured
"""
Show Event outputs.
"""


import json
import sys
import traceback

try:
    import dateutil.parser
except ImportError:
    def date_string(ds, fmt=''):  # pylint: disable=unused-argument
        return ds
else:
    def date_string(ds, fmt='%Y-%m-%d %H:%M:%S.%f'):
        d = dateutil.parser.parse(ds).astimezone(dateutil.tz.tzutc())
        return d.strftime(fmt)


def display(message):  # pylint: disable=missing-function-docstring
    print('{} - {}'.format(date_string(message['time']), message['event_type']))
    if message.get('event'):
        event = json.loads(message['event'])
        for k in sorted(event):
            print(f'\t{k}: {event[k]}')
    print()

while 1:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        obj = json.loads(line)
        display(obj)
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
        continue
