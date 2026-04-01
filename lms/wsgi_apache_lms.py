"""
Apache WSGI file for LMS

This module contains the WSGI application used for Apache deployment.
It exposes a module-level variable named ``application``.
"""

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs
defuse_xml_libs()

import os  # lint-amnesty, pylint: disable=wrong-import-order, wrong-import-position

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lms.envs.aws")

# This application object is used by the development server
# as well as any WSGI server configured to use this file.
from django.core.wsgi import get_wsgi_application  # lint-amnesty, pylint: disable=wrong-import-order, wrong-import-position
application = get_wsgi_application()  # pylint: disable=invalid-name
