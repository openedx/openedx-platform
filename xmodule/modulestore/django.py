"""
Module that provides a connection to the ModuleStore specified in the django settings.

Passes settings.MODULESTORE as kwargs to MongoModuleStore
"""

from contextlib import contextmanager
from importlib import import_module
import importlib.resources as resources
import gettext
import logging

import re  # lint-amnesty, pylint: disable=wrong-import-order

from django.conf import settings

# This configuration must be executed BEFORE any additional Django imports. Otherwise, the imports may fail due to
# Django not being configured properly. This mostly applies to tests.
if not settings.configured:
    settings.configure()

from django.contrib.staticfiles.storage import staticfiles_storage  # lint-amnesty, pylint: disable=wrong-import-position
from django.core.cache import caches, InvalidCacheBackendError  # lint-amnesty, pylint: disable=wrong-import-position
import django.dispatch  # lint-amnesty, pylint: disable=wrong-import-position
import django.utils  # lint-amnesty, pylint: disable=wrong-import-position
from django.utils.translation import get_language, to_locale  # lint-amnesty, pylint: disable=wrong-import-position
from edx_django_utils.cache import DEFAULT_REQUEST_CACHE  # lint-amnesty, pylint: disable=wrong-import-position

from xmodule.contentstore.django import contentstore  # lint-amnesty, pylint: disable=wrong-import-position
from xmodule.modulestore.draft_and_published import BranchSettingMixin  # lint-amnesty, pylint: disable=wrong-import-position
from xmodule.modulestore.mixed import MixedModuleStore  # lint-amnesty, pylint: disable=wrong-import-position
from xmodule.util.xmodule_django import get_current_request_hostname  # lint-amnesty, pylint: disable=wrong-import-position

from .api import (  # lint-amnesty, pylint: disable=wrong-import-position
    get_javascript_i18n_file_name,
    get_javascript_i18n_file_path,
    get_python_locale_root,
    get_xblock_root_module_name,
)

# We also may not always have the current request user (crum) module available
try:
    from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
    from crum import get_current_user

    HAS_USER_SERVICE = True
except ImportError:
    HAS_USER_SERVICE = False

try:
    from common.djangoapps.xblock_django.api import disabled_xblocks
except ImportError:
    disabled_xblocks = None

log = logging.getLogger(__name__)
ASSET_IGNORE_REGEX = getattr(settings, "ASSET_IGNORE_REGEX", r"(^\._.*$)|(^\.DS_Store$)|(^.*~$)")


class SwitchedSignal(django.dispatch.Signal):
    """
    SwitchedSignal is like a normal Django signal, except that you can turn it
    on and off. This is especially useful for tests where we want to be able to
    isolate signals and disable expensive operations that are irrelevant to
    what's being tested (like everything that triggers off of a course publish).

    SwitchedSignals default to being on. You should be very careful if you ever
    turn one off -- the only instances of this class are shared class attributes
    of `SignalHandler`. You have to make sure that you re-enable the signal when
    you're done, or else you may permanently turn that signal off for that
    process. I can't think of any reason you'd want to disable signals outside
    of running tests.
    """
    def __init__(self, name, *args, **kwargs):
        """
        The `name` parameter exists only to make debugging more convenient.

        All other args are passed to the constructor for django.dispatch.Signal.
        """
        super().__init__(*args, **kwargs)
        self.name = name
        self._allow_signals = True

    def disable(self):
        """
        Turn off signal sending.

        All calls to send/send_robust will no-op.
        """
        self._allow_signals = False

    def enable(self):
        """
        Turn on signal sending.

        Calls to send/send_robust will behave like normal Django Signals.
        """
        self._allow_signals = True

    @contextmanager
    def for_state(self, *, is_enabled: bool):
        """
        Set signal handling to be on or off for the duration of the context.
        """
        old_state = self._allow_signals
        try:
            self._allow_signals = is_enabled
            yield
        finally:
            self._allow_signals = old_state

    def send(self, *args, **kwargs):
        """
        See `django.dispatch.Signal.send()`

        This method will no-op and return an empty list if the signal has been
        disabled.
        """
        log.debug(
            "SwitchedSignal %s's send() called with args %s, kwargs %s - %s",
            self.name,
            args,
            kwargs,
            "ALLOW" if self._allow_signals else "BLOCK"
        )
        if self._allow_signals:
            return super().send(*args, **kwargs)
        return []

    def send_robust(self, *args, **kwargs):
        """
        See `django.dispatch.Signal.send_robust()`

        This method will no-op and return an empty list if the signal has been
        disabled.
        """
        log.debug(
            "SwitchedSignal %s's send_robust() called with args %s, kwargs %s - %s",
            self.name,
            args,
            kwargs,
            "ALLOW" if self._allow_signals else "BLOCK"
        )
        if self._allow_signals:
            return super().send_robust(*args, **kwargs)
        return []

    def __repr__(self):
        return f"SwitchedSignal('{self.name}')"


class SignalHandler:
    """
    This class is to allow the modulestores to emit signals that can be caught
    by other parts of the Django application. If your app needs to do something
    every time a course is published (e.g. search indexing), you can listen for
    that event and kick off a celery task when it happens.

    To listen for a signal, do the following::

        from django.dispatch import receiver
        from celery import shared_task
        from edx_django_utils.monitoring import set_code_owner_attribute
        from xmodule.modulestore.django import modulestore, SignalHandler

        @receiver(SignalHandler.course_published)
        def listen_for_course_publish(sender, course_key, **kwargs):
            do_my_expensive_update.delay(course_key)

        @shared_task()
        @set_code_owner_attribute
        def do_my_expensive_update(course_key):
            # ...

    Things to note:

    1. We receive using the Django Signals mechanism.
    2. The sender is going to be the class of the modulestore sending it.
    3. The names of your handler function's parameters *must* be "sender" and "course_key".
    4. Always have **kwargs in your signal handler, as new things may be added.
    5. The thing that listens for the signal lives in process, but should do
       almost no work. Its main job is to kick off the celery task that will
       do the actual work.
    """

    # If you add a new signal, please don't forget to add it to the _mapping
    # as well.
    pre_publish = SwitchedSignal("pre_publish")
    course_published = SwitchedSignal("course_published")
    course_deleted = SwitchedSignal("course_deleted")
    library_updated = SwitchedSignal("library_updated")
    item_deleted = SwitchedSignal("item_deleted")

    _mapping = {
        signal.name: signal
        for signal
        in [pre_publish, course_published, course_deleted, library_updated, item_deleted]
    }

    def __init__(self, modulestore_class):
        self.modulestore_class = modulestore_class

    @classmethod
    def all_signals(cls):
        """Return a list with all our signals in it."""
        return cls._mapping.values()

    @classmethod
    def signal_by_name(cls, signal_name):
        """Given a signal name, return the appropriate signal."""
        return cls._mapping[signal_name]

    def send(self, signal_name, **kwargs):
        """
        Send the signal to the receivers.
        """
        signal = self._mapping[signal_name]
        responses = signal.send_robust(sender=self.modulestore_class, **kwargs)

        for receiver, response in responses:
            log.info('Sent %s signal to %s with kwargs %s. Response was: %s', signal_name, receiver, kwargs, response)


# to allow easy imports
globals().update({sig.name.upper(): sig for sig in SignalHandler.all_signals()})


def load_function(path):
    """
    Load a function by name.

    Arguments:
        path: String of the form 'path.to.module.function'. Strings of the form
            'path.to.module:Class.function' are also valid.

    Returns:
        The imported object 'function'.
    """
    if ':' in path:
        module_path, _, method_path = path.rpartition(':')
        module = import_module(module_path)

        class_name, method_name = method_path.split('.')
        _class = getattr(module, class_name)
        function = getattr(_class, method_name)
    else:
        module_path, _, name = path.rpartition('.')
        function = getattr(import_module(module_path), name)

    return function


def create_modulestore_instance(
        engine,
        content_store,
        doc_store_config,
        options,
        i18n_service=None,
        fs_service=None,
        user_service=None,
        signal_handler=None,
):
    """
    This will return a new instance of a modulestore given an engine and options
    """
    # Import is placed here to avoid model import at project startup.
    import xblock.reference.plugins

    class_ = load_function(engine)

    _options = {}
    _options.update(options)

    FUNCTION_KEYS = ['render_template']
    for key in FUNCTION_KEYS:
        if key in _options and isinstance(_options[key], str):
            _options[key] = load_function(_options[key])

    request_cache = DEFAULT_REQUEST_CACHE

    try:
        metadata_inheritance_cache = caches['mongo_metadata_inheritance']
    except InvalidCacheBackendError:
        metadata_inheritance_cache = caches['default']

    if issubclass(class_, MixedModuleStore):
        _options['create_modulestore_instance'] = create_modulestore_instance

    if issubclass(class_, BranchSettingMixin):
        _options.setdefault('branch_setting_func', _get_modulestore_branch_setting)

    if HAS_USER_SERVICE and not user_service:
        xb_user_service = DjangoXBlockUserService(get_current_user())
    else:
        xb_user_service = None

    xblock_field_data_wrappers = [load_function(path) for path in settings.XBLOCK_FIELD_DATA_WRAPPERS]

    def fetch_disabled_xblock_types():
        """
        Get the disabled xblock names, using the request_cache if possible to avoid hitting
        a database every time the list is needed.
        """
        # If the import could not be loaded, return an empty list.
        if disabled_xblocks is None:
            return []

        if 'disabled_xblock_types' not in request_cache.data:
            request_cache.data['disabled_xblock_types'] = [block.name for block in disabled_xblocks()]
        return request_cache.data['disabled_xblock_types']

    return class_(
        contentstore=content_store,
        metadata_inheritance_cache_subsystem=metadata_inheritance_cache,
        request_cache=request_cache,
        xblock_mixins=getattr(settings, 'XBLOCK_MIXINS', ()),
        xblock_field_data_wrappers=xblock_field_data_wrappers,
        disabled_xblock_types=fetch_disabled_xblock_types,
        doc_store_config=doc_store_config,
        i18n_service=i18n_service or XBlockI18nService,
        fs_service=fs_service or xblock.reference.plugins.FSService(),
        user_service=user_service or xb_user_service,
        signal_handler=signal_handler or SignalHandler(class_),
        **_options
    )


def modulestore():
    """
    Returns the Mixed modulestore
    """
    mixed_modulestore = create_modulestore_instance(
        settings.MODULESTORE['default']['ENGINE'],
        contentstore(),
        settings.MODULESTORE['default'].get('DOC_STORE_CONFIG', {}),
        settings.MODULESTORE['default'].get('OPTIONS', {})
    )
    if settings.FEATURES.get('CUSTOM_COURSES_EDX'):
        # TODO: This import prevents a circular import issue, but is
        # symptomatic of a lib having a dependency on code in lms.  This
        # should be updated to have a setting that enumerates modulestore
        # wrappers and then uses that setting to wrap the modulestore in
        # appropriate wrappers depending on enabled features.
        from lms.djangoapps.ccx.modulestore import CCXModulestoreWrapper
        mixed_modulestore = CCXModulestoreWrapper(mixed_modulestore)

    return mixed_modulestore


class XBlockI18nService:
    """
    Implement the XBlock runtime "i18n" service.

    Mostly a pass-through to Django's translation module.
    django.utils.translation implements the gettext.Translations interface (it
    has ugettext, ungettext, etc), so we can use it directly as the runtime
    i18n service.

    This service supports OEP-58 translations (https://docs.openedx.org/en/latest/developers/concepts/oep58.html)
    that are pulled via atlas.
    """
    def __init__(self, block=None):
        """
        Attempt to load an XBlock-specific GNU gettext translation using the XBlock's own domain
        translation catalog.
        If we can't locate the domain translation catalog then we fall-back onto
        django.utils.translation, which will point to the system's own domain translation catalog
        This effectively achieves translations by coincidence for an XBlock which does not provide
        its own dedicated translation catalog along with its implementation.
        """
        self.translator = django.utils.translation
        if block:
            xblock_locale_domain, xblock_locale_dir = self.get_python_locale(block)
            selected_language = get_language()

            if xblock_locale_dir:
                try:
                    self.translator = gettext.translation(
                        xblock_locale_domain,
                        xblock_locale_dir,
                        [to_locale(selected_language if selected_language else settings.LANGUAGE_CODE)]
                    )
                except OSError:
                    # Fall back to the default Django translator if the XBlock translator is not found.
                    pass

    def get_python_locale(self, block):
        """
        Return the XBlock locale directory with the domain name.

        Return:
            (domain, locale_path): A tuple of the domain name and the XBlock locale directory.

        This method looks for translations in two locations:
            - First it looks for `atlas` translations in get_python_locale_root().
            - Alternatively, it looks for bundled translations in the XBlock pip package which are
              found at <python_environment_xblock_root>/conf/locale/<language>/LC_MESSAGES/<domain>.po|mo
        """
        xblock_module_name = get_xblock_root_module_name(block)
        xblock_locale_path = get_python_locale_root() / xblock_module_name

        # OEP-58 translations are pulled via atlas and takes precedence if exists.
        if xblock_locale_path.isdir():
            # The `django` domain is used for XBlocks consistent with the other repositories.
            return 'django', xblock_locale_path

        # Pre-OEP-58 translations within the XBlock pip packages are deprecated but supported.
        with resources.as_file(resources.files(xblock_module_name) / 'translations') as deprecated_xblock_locale_path:
            # The `text` domain was used for XBlocks pre-OEP-58.
            return 'text', str(deprecated_xblock_locale_path)

    def get_javascript_i18n_catalog_url(self, block):
        """
        Return the XBlock compiled JavaScript translations catalog static url.

        Return:
            str: The static url to the JavaScript translations catalog, otherwise None.
        """
        xblock_module_name = get_xblock_root_module_name(block)
        language_name = get_language()  # Returns language name e.g. `de` or `de-de`.
        locale = to_locale(language_name)  # Use the `de` or `de_DE` format for the locale directory.

        if get_javascript_i18n_file_path(xblock_module_name, locale).exists():
            relative_file_path = get_javascript_i18n_file_name(xblock_module_name, locale)
            return staticfiles_storage.url(relative_file_path)
        return None

    def __getattr__(self, name):
        name = 'gettext' if name == 'ugettext' else name
        return getattr(self.translator, name)

    def strftime(self, *args, **kwargs):
        """
        A locale-aware implementation of strftime.
        """
        # This is the wrong place to import this function.  I'm putting it here
        # because the xmodule test suite can't import this module, because
        # Django is not available in that suite.  This function isn't called in
        # that suite, so this hides the import so the test won't fail.
        #
        # As I said, this is wrong.  But Cale says this code will soon be
        # refactored to a place that will be right, and the code can be made
        # right there.  If you are reading this comment after April 1, 2014,
        # then Cale was a liar.
        from common.djangoapps.util.date_utils import strftime_localized

        return strftime_localized(*args, **kwargs)


def _get_modulestore_branch_setting():
    """
    Returns the branch setting for the module store from the current Django request if configured,
    else returns the branch value from the configuration settings if set,
    else returns None

    The value of the branch setting is cached in a thread-local variable so it is not repeatedly recomputed
    """

    def get_branch_setting():
        """
        Finds and returns the branch setting based on the Django request and the configuration settings
        """
        branch = None
        hostname = get_current_request_hostname()
        if hostname:
            # get mapping information which is defined in configurations
            mappings = getattr(settings, 'HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS', None)

            # compare hostname against the regex expressions set of mappings which will tell us which branch to use
            if mappings:
                for key in mappings:
                    if re.match(key, hostname):
                        return mappings[key]
        if branch is None:
            branch = getattr(settings, 'MODULESTORE_BRANCH', None)
        return branch

    # leaving this in code structured in closure-friendly format b/c we might eventually cache this (again)
    # using request_cache
    return get_branch_setting()
