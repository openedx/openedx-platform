# lint-amnesty, pylint: disable=cyclic-import, missing-module-docstring
from django.conf import settings  # noqa: I001

if hasattr(settings, "COMMENTS_SERVICE_URL"):
    SERVICE_HOST = settings.COMMENTS_SERVICE_URL
else:
    SERVICE_HOST = 'http://localhost:4567'

PREFIX = SERVICE_HOST + '/api/v1'
