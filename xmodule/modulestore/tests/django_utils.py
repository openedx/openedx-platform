"""
Modulestore configuration for test cases.
"""


import copy
import functools
import os
from contextlib import contextmanager
from enum import Enum
from mimetypes import guess_type
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connections, transaction
from django.test import TestCase
from django.test.utils import override_settings
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import _CONTENTSTORE
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.tests.factories import XMODULE_FACTORY_LOCK
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM

from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from openedx.core.djangolib.testing.utils import CacheIsolationMixin, CacheIsolationTestCase, FilteredQueryCountMixin
from openedx.core.lib.tempdir import mkdtemp_clean
from common.djangoapps.split_modulestore_django.models import SplitModulestoreCourseIndex
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory, InstructorFactory
from common.djangoapps.student.tests.factories import StaffFactory


class CourseUserType(Enum):
    """
    Types of users to be used when testing a course.
    """
    ANONYMOUS = 'anonymous'
    COURSE_STAFF = 'course_staff'
    COURSE_INSTRUCTOR = 'course_instructor'
    ENROLLED = 'enrolled'
    GLOBAL_STAFF = 'global_staff'
    UNENROLLED = 'unenrolled'
    UNENROLLED_STAFF = 'unenrolled_staff'


class StoreConstructors:
    """Enumeration of store constructor types."""
    draft, split = list(range(2))


def mixed_store_config(data_dir, mappings, store_order=None, modulestore_options=None):
    """
    Return a `MixedModuleStore` configuration, which provides
    access to both Mongo-backed courses.

    Args:
        data_dir (string): the directory from which to load XML-backed courses.
        mappings (string): a dictionary mapping course IDs to modulestores, for example:

            {
                'MITx/2.01x/2013_Spring': 'xml',
                'edx/999/2013_Spring': 'default'
            }

        where 'xml' and 'default' are the two options provided by this configuration,
        mapping (respectively) to XML-backed and Mongo-backed modulestores..

    Keyword Args:

        store_order (list): List of StoreConstructors providing order of modulestores
            to use in creating courses.
    """
    if store_order is None:
        store_order = [StoreConstructors.draft, StoreConstructors.split]

    options = {
        'default_class': 'xmodule.hidden_block.HiddenBlock',
        'fs_root': data_dir,
        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
    }
    if modulestore_options:
        options.update(modulestore_options)

    store_constructors = {
        StoreConstructors.split: split_mongo_store_config(options)['default'],
        StoreConstructors.draft: draft_mongo_store_config(options)['default'],
    }

    store = {
        'default': {
            'ENGINE': 'xmodule.modulestore.mixed.MixedModuleStore',
            'OPTIONS': {
                'mappings': mappings,
                'stores': [store_constructors[store] for store in store_order],
            }
        }
    }
    return store


def draft_mongo_store_config(modulestore_options):
    """
    Defines default module store using DraftMongoModuleStore.
    """
    store = {
        'default': {
            'NAME': 'draft',
            'ENGINE': 'xmodule.modulestore.mongo.draft.DraftModuleStore',
            'DOC_STORE_CONFIG': {
                'host': MONGO_HOST,
                'port': MONGO_PORT_NUM,
                'db': f'test_xmodule_{os.getpid()}',
                'collection': 'modulestore',
            },
            'OPTIONS': modulestore_options
        }
    }

    return store


def split_mongo_store_config(modulestore_options):
    """
    Defines split module store.
    """
    store = {
        'default': {
            'NAME': 'draft',
            'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
            'DOC_STORE_CONFIG': {
                'host': MONGO_HOST,
                'port': MONGO_PORT_NUM,
                'db': f'test_xmodule_{os.getpid()}',
                'collection': 'modulestore',
            },
            'OPTIONS': modulestore_options
        }
    }

    return store


def contentstore_config():
    """
    Return a new configuration for the contentstore that is isolated
    from other such configurations.
    """
    return {
        'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
        'DOC_STORE_CONFIG': {
            'host': MONGO_HOST,
            'db': f'test_xcontent_{os.getpid()}',
            'port': MONGO_PORT_NUM,
        },
        # allow for additional options that can be keyed on a name, e.g. 'trashcan'
        'ADDITIONAL_OPTIONS': {
            'trashcan': {
                'bucket': 'trash_fs'
            }
        }
    }


@patch('xmodule.modulestore.django.create_modulestore_instance', autospec=True)
def drop_mongo_collections(mock_create):
    """
    If using a Mongo-backed modulestore & contentstore, drop the collections.
    """
    # Do not create the modulestore if it does not exist.
    mock_create.return_value = None

    module_store = modulestore()
    if hasattr(module_store, '_drop_database'):
        module_store._drop_database(database=False)  # pylint: disable=protected-access
    _CONTENTSTORE.clear()
    if hasattr(module_store, 'close_connections'):
        module_store.close_connections()


TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT

# This modulestore will provide a mixed mongo editable modulestore.
# If your test uses the 'toy' course, use the the ToyCourseFactory to construct it.
# If your test needs a closed course to test against, import the common/test/data/2014
#   test course into this modulestore.
# If your test needs a graded course to test against, import the common/test/data/graded
#   test course into this modulestore.
TEST_DATA_MIXED_MODULESTORE = functools.partial(
    mixed_store_config,
    TEST_DATA_DIR,
    {}
)

# All store requests now go through mixed
# Use this modulestore if you specifically want to test mongo and not a mocked modulestore.
TEST_DATA_MONGO_MODULESTORE = functools.partial(mixed_store_config, mkdtemp_clean(), {})

# When switching away from MONGO-by-default to SPLIT-by-default, we broke a lot of tests.
# Some we fixed, but others we did not fully investigate. This value was used for those.
# These tests may actually need MONGO, or they may be able to be fixed to work in SPLIT. Investigation needed.
TEST_DATA_MONGO_AMNESTY_MODULESTORE = TEST_DATA_MONGO_MODULESTORE

# All store requests now go through mixed
# Use this modulestore if you specifically want to test split-mongo and not a mocked modulestore.
TEST_DATA_SPLIT_MODULESTORE = functools.partial(
    mixed_store_config,
    mkdtemp_clean(),
    {},
    store_order=[StoreConstructors.split, StoreConstructors.draft]
)

# Tests that use mixed modulestore and split, but don't load/use draft modulestore.
# This also enables "draft preferred" mode, like Studio.
TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED = functools.partial(
    mixed_store_config,
    mkdtemp_clean(),
    {},
    store_order=[StoreConstructors.split],
    modulestore_options={'branch_setting_func': lambda: ModuleStoreEnum.Branch.draft_preferred},
)


class SignalIsolationMixin:
    """
    Simple utility mixin class to toggle ModuleStore signals on and off. This
    class operates on `SwitchedSignal` objects on the modulestore's
    `SignalHandler`.
    """
    @classmethod
    def disable_all_signals(cls):
        """Disable all signals in the modulestore's SignalHandler."""
        for signal in SignalHandler.all_signals():
            signal.disable()

    @classmethod
    def enable_all_signals(cls):
        """Enable all signals in the modulestore's SignalHandler."""
        for signal in SignalHandler.all_signals():
            signal.enable()

    @classmethod
    def enable_signals_by_name(cls, *signal_names):
        """
        Enable specific signals in the modulestore's SignalHandler.

        Arguments:
            signal_names (list of `str`): Names of signals to enable.
        """
        for signal_name in signal_names:
            try:
                signal = SignalHandler.signal_by_name(signal_name)
            except KeyError:
                all_signal_names = sorted(s.name for s in SignalHandler.all_signals())
                err_msg = (
                    "You tried to enable signal '{}', but I don't recognize that "
                    "signal name. Did you mean one of these?: {}"
                )
                raise ValueError(err_msg.format(signal_name, all_signal_names))  # lint-amnesty, pylint: disable=raise-missing-from
            signal.enable()


class ModuleStoreIsolationMixin(CacheIsolationMixin, SignalIsolationMixin):
    """
    A mixin to be used by TestCases that want to isolate their use of the
    Modulestore.

    How to use::

        class MyTestCase(ModuleStoreMixin, TestCase):

            MODULESTORE = <settings for the modulestore to test>

            ENABLED_SIGNALS = ['course_published']

            def my_test(self):
                self.start_modulestore_isolation()
                self.addCleanup(self.end_modulestore_isolation)

                modulestore.create_course(...)
                ...

    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    CONTENTSTORE = functools.partial(contentstore_config)
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache', 'course_index_cache']

    # List of modulestore signals enabled for this test. Defaults to an empty
    # list. The list of signals available is found on the SignalHandler class,
    # in /xmodule/modulestore/django.py
    #
    # You must use the signal itself, and not its name. So for example:
    #
    # class MyPublishTestCase(ModuleStoreTestCase):
    #     ENABLED_SIGNALS = ['course_published', 'pre_publish']
    #
    ENABLED_SIGNALS = []

    __settings_overrides = []
    __old_modulestores = []
    __old_contentstores = []

    @classmethod
    def start_modulestore_isolation(cls):
        """
        Isolate uses of the modulestore after this call. Once
        :py:meth:`end_modulestore_isolation` is called, this modulestore will
        be flushed (all content will be deleted).
        """
        cls.disable_all_signals()
        cls.enable_signals_by_name(*cls.ENABLED_SIGNALS)
        cls.start_cache_isolation()
        override = override_settings(
            MODULESTORE=cls.MODULESTORE(),
            CONTENTSTORE=cls.CONTENTSTORE(),
        )

        cls.__old_modulestores.append(copy.deepcopy(settings.MODULESTORE))
        cls.__old_contentstores.append(copy.deepcopy(settings.CONTENTSTORE))
        override.__enter__()  # pylint: disable=unnecessary-dunder-call
        cls.__settings_overrides.append(override)
        XMODULE_FACTORY_LOCK.enable()
        cls.store = modulestore()

    @classmethod
    def end_modulestore_isolation(cls):
        """
        Delete all content in the Modulestore, and reset the Modulestore
        settings from before :py:meth:`start_modulestore_isolation` was
        called.
        """
        drop_mongo_collections()  # pylint: disable=no-value-for-parameter
        XMODULE_FACTORY_LOCK.disable()
        cls.__settings_overrides.pop().__exit__(None, None, None)

        assert settings.MODULESTORE == cls.__old_modulestores.pop()
        assert settings.CONTENTSTORE == cls.__old_contentstores.pop()
        cls.end_cache_isolation()
        cls.enable_all_signals()

    @staticmethod
    def allow_transaction_exception():
        """
        Context manager to wrap modulestore-using test code that may throw an exception.

        (Use this if a modulestore test is failing with TransactionManagementError during cleanup.)

        Details:
        Some test cases that purposely throw an exception may normally cause the end_modulestore_isolation() cleanup
        step to fail with
            TransactionManagementError:
            An error occurred in the current transaction. You can't execute queries until the end of the 'atomic' block.
        This happens because the test is wrapped in an implicit transaction and when the exception occurs, django won't
        allow any subsequent database queries in the same transaction - in particular, the queries needed to clean up
        split modulestore's SplitModulestoreCourseIndex table after the test.

        By wrapping the inner part of the test in this atomic() call, we create a savepoint so that if an exception is
        thrown, Django merely rolls back to the savepoint and the overall transaction continues, including the eventual
        cleanup step.

        This method mostly exists to provide this docstring/explanation; the code itself is trivial.
        """
        return transaction.atomic()


class ModuleStoreTestUsersMixin():
    """
    A mixin to help manage test users.
    """
    TEST_PASSWORD = 'Password1234'

    def create_user_for_course(self, course, user_type=CourseUserType.ENROLLED):
        """
        Create a test user for a course.
        """
        if user_type is CourseUserType.ANONYMOUS:
            self.client.logout()
            return AnonymousUser()

        is_enrolled = user_type is CourseUserType.ENROLLED

        # Set up the test user
        if user_type is CourseUserType.UNENROLLED_STAFF:
            user = StaffFactory(course_key=course.id, password=self.TEST_PASSWORD)
        elif user_type is CourseUserType.GLOBAL_STAFF:
            user = AdminFactory(password=self.TEST_PASSWORD)
        elif user_type is CourseUserType.COURSE_INSTRUCTOR:
            user = InstructorFactory(course_key=course.id, password=self.TEST_PASSWORD)
        else:
            user = UserFactory(password=self.TEST_PASSWORD)

        self.client.login(username=user.username, password=self.TEST_PASSWORD)

        if is_enrolled:
            CourseEnrollment.enroll(user, course.id)

        return user


class SharedModuleStoreTestCase(
    ModuleStoreTestUsersMixin, FilteredQueryCountMixin, ModuleStoreIsolationMixin, CacheIsolationTestCase
):
    """
    Subclass for any test case that uses a ModuleStore that can be shared
    between individual tests. This class ensures that the ModuleStore is cleaned
    before/after the entire test case has run. Use this class if your tests
    set up one or a small number of courses that individual tests do not modify.
    If your tests modify contents in the ModuleStore, you should use
    ModuleStoreTestCase instead.

    How to use::

        from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
        from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory

        class MyModuleStoreTestCase(SharedModuleStoreTestCase):
            @classmethod
            def setUpClass(cls):
                super(MyModuleStoreTestCase, cls).setUpClass()
                cls.course = CourseFactory.create()

            def setUp(self):
                super(MyModuleStoreTestCase, self).setUp()
                self.user = UserFactory.create()
                CourseEnrollmentFactory.create(
                    user=self.user, course_id=self.course.id
                )

    Important things to note:

    1. You're creating the course in setUpClass(), *not* in setUp().
    2. Any Django ORM operations should still happen in setUp(). Models created
       in setUpClass() will *not* be cleaned up, and will leave side-effects
       that can break other, completely unrelated test cases.

    In Django 1.8, we will be able to use setUpTestData() to do class level init
    for Django ORM models that will get cleaned up properly.
    """
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    @classmethod
    @contextmanager
    def setUpClassAndTestData(cls):  # pylint: disable=invalid-name
        """
        For use when the test class has a setUpTestData() method that uses variables
        that are setup during setUpClass() of the same test class.
        Use it like so:
        @classmethod
        def setUpClass(cls):
            # pylint: disable=super-method-not-called
            with super(MyTestClass, cls).setUpClassAndTestData():
                <all the cls.setUpClass() setup code that performs modulestore setup...>
        @classmethod
        def setUpTestData(cls):
            <all the setup code that creates Django models per test class...>
            <these models can use variables (courses) setup in setUpClass() above>
        """
        cls.start_modulestore_isolation()
        # Now yield to allow the test class to run its setUpClass() setup code.
        yield
        # Now call the base class, which calls back into the test class's setUpTestData().
        super().setUpClass()

    @classmethod
    def setUpClass(cls):
        """
        Start modulestore isolation, and then load modulestore specific
        test data.
        """
        super().setUpClass()
        cls.start_modulestore_isolation()

    @classmethod
    def tearDownClass(cls):
        cls.end_modulestore_isolation()
        super().tearDownClass()

        # Overly broad hammer that breaks abstraction barrier to clear data from
        # the table underlying the Django ORM backed modulestore active versions
        # lookup. This has to go _after_ the super().tearDownClass call above,
        # or it doesn't work.
        SplitModulestoreCourseIndex.objects.all().delete()

    def setUp(self):
        # OverrideFieldData.provider_classes is always reset to `None` so
        # that they're recalculated for every test
        OverrideFieldData.provider_classes = None
        super().setUp()


class ModuleStoreTestCase(
    ModuleStoreTestUsersMixin, FilteredQueryCountMixin, ModuleStoreIsolationMixin, TestCase
):
    """
    Subclass for any test case that uses a ModuleStore.
    Ensures that the ModuleStore is cleaned before/after each test.

    Usage:

        1. Create a subclass of `ModuleStoreTestCase`
        2. (optional) If you need a specific variety of modulestore, or particular ModuleStore
           options, set the MODULESTORE class attribute of your test class to the
           appropriate modulestore config.

           For example:

               class FooTest(ModuleStoreTestCase):
                   MODULESTORE = mixed_store_config(data_dir, mappings)
                   # ...

        3. Use factories (e.g. `CourseFactory`, `BlockFactory`) to populate
           the modulestore with test data.

    NOTE:
        * For Mongo-backed courses (created with `CourseFactory`),
          the state of the course will be reset before/after each
          test method executes.

        * For XML-backed courses, the course state will NOT
          reset between test methods (although it will reset
          between test classes)

          The reason is: XML courses are not editable, so to reset
          a course you have to reload it from disk, which is slow.
    """

    CREATE_USER = True

    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

        # Overly broad hammer that breaks abstraction barrier to clear data from
        # the table underlying the Django ORM backed modulestore active versions
        # lookup. This has to go _after_ the super().tearDownClass call above,
        # or it doesn't work.
        SplitModulestoreCourseIndex.objects.all().delete()

    def setUp(self):
        """
        Creates a test User if `self.CREATE_USER` is True.
        Sets the password as self.user_password.
        """
        self.start_modulestore_isolation()

        self.addCleanup(self.end_modulestore_isolation)

        # When testing CCX, we should make sure that
        # OverrideFieldData.provider_classes is always reset to `None` so
        # that they're recalculated for every test
        OverrideFieldData.provider_classes = None

        super().setUp()

        self.store = modulestore()

        uname = 'testuser'
        email = 'test+courses@edx.org'
        self.user_password = 'foo'

        if self.CREATE_USER:
            # Create the user so we can log them in.
            self.user = UserFactory.create(username=uname, email=email, password=self.user_password)

            # Note that we do not actually need to do anything
            # for registration if we directly mark them active.
            self.user.is_active = True

            # Staff has access to view all courses
            self.user.is_staff = True
            self.user.save()

    def create_non_staff_user(self):
        """
        Creates a non-staff test user.
        Returns the non-staff test user and its password.
        """
        uname = 'teststudent'
        password = 'foo'
        nonstaff_user = UserFactory.create(username=uname, email='test+student@edx.org', password=password)

        # Note that we do not actually need to do anything
        # for registration if we directly mark them active.
        nonstaff_user.is_active = True
        nonstaff_user.is_staff = False
        nonstaff_user.save()
        return nonstaff_user, password

    def update_course(self, course, user_id):
        """
        Updates the version of course in the modulestore

        'course' is an instance of CourseBlock for which we want
        to update metadata.
        """
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
            self.store.update_item(course, user_id)
        updated_course = self.store.get_course(course.id)
        return updated_course


class ImmediateOnCommitMixin:
    """
    Mixin for tests that want `on_commit` callbacks to run immediately,
    even under TestCase (which normally wraps tests in a transaction
    that never commits).
    Especially useful when the test needs to execute an event that occurs after an `on_commit`
    """

    @classmethod
    def setUpClass(cls):
        super_cls = super()
        if hasattr(super_cls, 'setUpClass'):
            super_cls.setUpClass()
        # Patch `transaction.on_commit` so that callbacks run immediately
        cls._on_commit_patcher = patch(
            'django.db.transaction.on_commit',
            side_effect=lambda func, **kwargs: func()
        )
        cls._on_commit_patcher.start()

    @classmethod
    def tearDownClass(cls):
        # Stop patching, restore original behavior
        cls._on_commit_patcher.stop()
        super_cls = super()
        if hasattr(super_cls, 'tearDownClass'):
            super_cls.tearDownClass()


def upload_file_to_course(course_key, contentstore, source_file, target_filename):
    '''
    Uploads the given source file to the given course, and returns the content of the file.
    '''
    asset_key = course_key.make_asset_key('asset', target_filename)
    with open(source_file, "rb") as f:
        file_contents = f.read()
    mimetype = guess_type(source_file)[0]
    content = StaticContent(asset_key, target_filename, mimetype, file_contents, locked=False)
    contentstore.save(content)
    return file_contents
