# pylint: disable=missing-module-docstring
def serialize_datetime(d):
    return d.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
