"""Instantiate the singleton Celery instance that is used by either lms or cms.

WARNING: Do not import this module directly!

This module should only be imported by cms.celery and lms.celery, which perform
setup in a particular order before and after Celery is instantiated. Otherwise,
it might be possible for the Celery singleton to be created without variant-
specific configuration.

The module is intended as a way to have a Celery singleton shared between cms
and lms code. Not having a guaranteed singleton means that it is possible for
each of cms and lms to instantiate Celery, and tasks may be nondeterministically
registered to one or the other of the instances and therefore sometimes lost
to the running process. The root ``__init__.py``s of cms and lms both ensure that
this module is loaded when any cms or lms code runs, effectively using the
Python module system as a singleton loader. (This is an incremental improvement
over older code, and there is probably a better mechanism to be had.)
"""

from celery import Celery  # noqa: I001

# WARNING: Do not refer to this unless you are cms.celery or
# lms.celery. See module docstring!
APP = Celery('proj')

# Using a string here means the worker will not have to
# pickle the object when using Windows.
APP.config_from_object('django.conf:settings')
APP.autodiscover_tasks()
