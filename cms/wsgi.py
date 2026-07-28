# ruff: noqa: I001
"""
WSGI config for CMS.

This module contains the WSGI application used by Django's development server
and any production WSGI deployments.
It exposes a module-level variable named ``application``. Django's
``runserver`` and ``runfcgi`` commands discover this application via the
``WSGI_APPLICATION`` setting.

Import sorting is intentionally disabled in this module.
"""

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs
defuse_xml_libs()

import os  # pylint: disable=wrong-import-order, wrong-import-position
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.envs.aws")

# This application object is used by the development server
# as well as any WSGI server configured to use this file.
from django.core.wsgi import get_wsgi_application  # pylint: disable=wrong-import-order, wrong-import-position
application = get_wsgi_application()
