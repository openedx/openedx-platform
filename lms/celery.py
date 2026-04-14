"""
Import celery, load its settings from the django settings
and auto discover tasks in all installed django apps.

Taken from: https://celery.readthedocs.org/en/latest/django/first-steps-with-django.html
"""

import os

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs

defuse_xml_libs()


# Set the default Django settings module for the 'celery' program
# and then instantiate the Celery singleton.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms.envs.production')
from openedx.core.lib.celery import APP  # pylint: disable=wrong-import-position,unused-import  # noqa: F401
