"""
Startup work shared by the LMS and Studio WSGI modules (``lms/wsgi.py`` and ``cms/wsgi.py``).

Web server workers import those modules once and then serve requests for a long time,
so it pays to do two things before the first request arrives:

* Warm up: load the code that every worker's first requests would otherwise load
  (the URLconf, and the modulestore classes along with every installed XBlock class).
* Tune garbage collection: startup leaves roughly 700,000 long-lived objects behind.
  With default settings, every full collection walks all of them again, which costs
  hundreds of milliseconds per collection. Freezing them after startup takes them out
  of every later collection.

The WSGI module is expected to call ``gc.disable()`` before it imports anything else,
so that no time is spent collecting during startup, and to call
``warm_up_service_and_freeze_gc()`` once the WSGI application exists.

Set ``WSGI_WARM_UP_SERVICE_AND_FREEZE_GC = False`` to turn all of this off. The collector is
still off while the application loads, but ``warm_up_service_and_freeze_gc()`` then only turns
it back on: nothing is warmed up or frozen, and the thresholds stay at Python's defaults.
"""
import gc
import logging
from importlib import import_module

log = logging.getLogger(__name__)

# Python's default first-generation threshold is 700. With startup objects frozen,
# raising it to 2,000 cut the remaining collection time by about a third in load tests.
# Higher values saved little more time, but let garbage pile up longer between
# collections: with several request threads per worker, 10,000 used about 45 MB more
# per LMS worker than 2,000.
GC_THRESHOLDS = (2000, 10, 10)


def warm_up_service_and_freeze_gc(thresholds=GC_THRESHOLDS):
    """
    Warm up the application, then freeze the startup heap and re-enable GC.

    Call this at the end of a WSGI module, after ``get_wsgi_application()``.
    Warm-up failures are logged, not raised: warming up is an optimization, and the
    same code will run again on the first request that needs it. GC is re-enabled
    either way.

    If ``settings.WSGI_WARM_UP_SERVICE_AND_FREEZE_GC`` is False, this only re-enables GC.
    """
    from django.conf import settings  # pylint: disable=import-outside-toplevel

    if not settings.WSGI_WARM_UP_SERVICE_AND_FREEZE_GC:
        gc.enable()
        log.info("WSGI_WARM_UP_SERVICE_AND_FREEZE_GC is off: skipping the WSGI warm-up and GC freeze.")
        return

    try:
        for step in (_load_urls, _import_stores):
            try:
                step()
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("WSGI warm-up step %s failed; the first requests will do this instead.", step.__name__)
    finally:
        # Free startup garbage first, so that it doesn't get frozen with everything else.
        gc.collect()
        gc.freeze()
        gc.set_threshold(*thresholds)
        gc.enable()


def _load_urls():
    """
    Import the URLconf and build the URL reverse-lookup table for the default language.
    """
    from django.urls import reverse  # pylint: disable=import-outside-toplevel

    reverse("heartbeat")


def _import_stores():
    """
    Import the modulestore and contentstore classes named in settings.

    Importing the modulestore loads every installed XBlock class. This only imports
    the classes and doesn't create the stores: some WSGI servers (uWSGI without
    ``lazy-apps``, gunicorn with ``--preload``) load the application once and then fork
    their workers, and MongoDB clients shouldn't be created before a fork. Creating the
    stores on the first request takes a few milliseconds once the classes are loaded.

    This only means that the warm-up adds no connections of its own. Loading the
    application can already open some before a fork: a monitoring middleware in
    edx-django-utils reads a waffle switch when it is created, which connects to the
    cache, and to the database too if the switch isn't cached.
    """
    from django.conf import settings  # pylint: disable=import-outside-toplevel

    modulestore_settings = settings.MODULESTORE["default"]
    engines = [modulestore_settings["ENGINE"], settings.CONTENTSTORE["ENGINE"]]
    engines += [store["ENGINE"] for store in modulestore_settings.get("OPTIONS", {}).get("stores", [])]
    for engine in engines:
        import_module(engine.rsplit(".", 1)[0])
