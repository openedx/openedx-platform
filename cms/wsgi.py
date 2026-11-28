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

# Turn off the cyclic garbage collector while the application loads. Startup creates
# hundreds of thousands of long-lived objects, and collecting during startup only walks
# them again and again. warm_up_service_and_freeze_gc() below turns it back on.
import gc
gc.disable()

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs  # pylint: disable=wrong-import-position
defuse_xml_libs()

import os  # pylint: disable=wrong-import-order, wrong-import-position
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.envs.aws")

# This application object is used by the development server
# as well as any WSGI server configured to use this file.
from django.core.wsgi import get_wsgi_application  # pylint: disable=wrong-import-order, wrong-import-position
application = get_wsgi_application()

# Load what the first requests would otherwise load, then freeze the startup heap so later
# garbage collections skip it, and turn the collector back on. With
# WSGI_WARM_UP_SERVICE_AND_FREEZE_GC = False, this only turns the collector back on.
from openedx.core.lib.wsgi_warmup import warm_up_service_and_freeze_gc  # pylint: disable=wrong-import-position
warm_up_service_and_freeze_gc()
