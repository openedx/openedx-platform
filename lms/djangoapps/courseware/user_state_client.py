"""
An implementation of :class:`XBlockUserStateClient`, which stores XBlock Scope.user_state
data in a Django ORM model.
"""


import itertools
import logging
from abc import abstractmethod
from collections import namedtuple
from operator import attrgetter
from time import time

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.paginator import Paginator
from django.db import transaction
from django.db.utils import IntegrityError
from edx_django_utils import monitoring as monitoring_utils
from xblock.fields import Scope

from lms.djangoapps.courseware.models import BaseStudentModuleHistory, StudentModule

try:
    import simplejson as json
except ImportError:
    import json


log = logging.getLogger(__name__)


class XBlockUserState(namedtuple('_XBlockUserState', ['username', 'block_key', 'state', 'updated', 'scope'])):
    """
    The current state of a single XBlock.

    Arguments:
        username: The username of the user that stored this state.
        block_key: The key identifying the scoped state. Depending on the :class:`~xblock.fields.BlockScope` of

                  ``scope``, this may take one of several types:

                      * ``USAGE``: :class:`~opaque_keys.edx.keys.UsageKey`
                      * ``DEFINITION``: :class:`~opaque_keys.edx.keys.DefinitionKey`
                      * ``TYPE``: :class:`str`
                      * ``ALL``: ``None``
        state: A dict mapping field names to the values of those fields for this XBlock.
        updated: A :class:`datetime.datetime`. We guarantee that the fields
                 that were returned in "state" have not been changed since
                 this time (in UTC).
        scope: A :class:`xblock.fields.Scope` identifying which XBlock scope this state is coming from.
    """
    __slots__ = ()

    def __repr__(self):
        return "{}{!r}".format(  # pylint: disable=consider-using-f-string  # noqa: UP032
            self.__class__.__name__,
            tuple(self)
        )


class XBlockUserStateClient():  # noqa: UP039
    """
    First stab at an interface for accessing XBlock User State. This will have
    use StudentModule as a backing store in the default case.

    Scope/Goals:

        1. Mediate access to all student-specific state stored by XBlocks.
            a. This includes "preferences" and "user_info" (i.e. UserScope.ONE)
            b. This includes XBlock Asides.
            c. This may later include user_state_summary (i.e. UserScope.ALL).
            d. This may include group state in the future.
            e. This may include other key types + UserScope.ONE (e.g. Definition)
        2. Assume network service semantics.
            At some point, this will probably be calling out to an external service.
            Even if it doesn't, we want to be able to implement circuit breakers, so
            that a failure in StudentModule doesn't bring down the whole site.
            This also implies that the client is running as a user, and whatever is
            backing it is smart enough to do authorization checks.
        3. This does not yet cover export-related functionality.
    """

    class ServiceUnavailable(Exception):
        """
        This error is raised if the service backing this client is currently unavailable.
        """

    class PermissionDenied(Exception):
        """
        This error is raised if the caller is not allowed to access the requested data.
        """

    class DoesNotExist(Exception):
        """
        This error is raised if the caller has requested data that does not exist.
        """

    def get(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key: The key identifying which xblock state to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Returns:
            XBlockUserState: The current state of the block for the specified username and block_key.

        Raises:
            DoesNotExist if no entry is found.
        """
        try:
            return next(self.get_many(username, [block_key], scope, fields=fields))
        except StopIteration as exception:
            raise self.DoesNotExist() from exception

    def set(self, username, block_key, state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key: The key identifying which xblock state to load.
            state (dict): A dictionary mapping field names to values
            scope (Scope): The scope to store data to
        """
        self.set_many(username, {block_key: state}, scope)

    def delete(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key: The key identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        return self.delete_many(username, [block_key], scope, fields=fields)

    @abstractmethod
    def get_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys: A list of keys identifying which xblock states to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Yields:
            XBlockUserState tuples for each specified key in block_keys.
            field_state is a dict mapping field names to values.
        """
        raise NotImplementedError()

    @abstractmethod
    def set_many(self, username, block_keys_to_state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys_to_state (dict): A dict mapping keys to state dicts.
                Each state dict maps field names to values. These state dicts
                are overlaid over the stored state. To delete fields, use
                :meth:`delete` or :meth:`delete_many`.
            scope (Scope): The scope to load data from
        """
        raise NotImplementedError()

    @abstractmethod
    def delete_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a many xblock usages.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key: The key identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        raise NotImplementedError()

    def get_history(self, username, block_key, scope=Scope.user_state):
        """
        Retrieve history of state changes for a given block for a given
        student.  We don't guarantee that history for many blocks will be fast.

        If the specified block doesn't exist, raise :class:`~DoesNotExist`.

        Arguments:
            username: The name of the user whose history should be retrieved.
            block_key: The key identifying which xblock history to retrieve.
            scope (Scope): The scope to load data from.

        Yields:
            XBlockUserState entries for each modification to the specified XBlock, from latest
            to earliest.
        """
        raise NotImplementedError()

    def iter_all_for_block(self, block_key, scope=Scope.user_state):
        """
        You get no ordering guarantees. If you're using this method, you should be running in an
        async task.
        """
        raise NotImplementedError()

    def iter_all_for_course(self, course_key, block_type=None, scope=Scope.user_state):
        """
        You get no ordering guarantees. If you're using this method, you should be running in an
        async task.
        """
        raise NotImplementedError()


class DjangoXBlockUserStateClient(XBlockUserStateClient):
    """
    An interface that uses the Django ORM StudentModule as a backend.

    A note on the format of state storage:
        The state for an xblock is stored as a serialized JSON dictionary. The model
        field that it is stored in can also take on a value of ``None``. To preserve
        existing analytic uses, we will preserve the following semantics:

        A state of ``None`` means that the user hasn't ever looked at the xblock.
        A state of ``"{}"`` means that the XBlock has at some point stored state for
           the current user, but that that state has been deleted.
        Otherwise, the dictionary contains all data stored for the user.

        None of these conditions should violate the semantics imposed by
        XBlockUserStateClient (for instance, once all fields have been deleted from
        an XBlock for a user, the state will be listed as ``None`` by :meth:`get_history`,
        even though the actual stored state in the database will be ``"{}"``).
    """

    class ServiceUnavailable(XBlockUserStateClient.ServiceUnavailable):
        """
        This error is raised if the service backing this client is currently unavailable.
        """
        pass  # pylint: disable=unnecessary-pass

    class PermissionDenied(XBlockUserStateClient.PermissionDenied):
        """
        This error is raised if the caller is not allowed to access the requested data.
        """
        pass  # pylint: disable=unnecessary-pass

    class DoesNotExist(XBlockUserStateClient.DoesNotExist):
        """
        This error is raised if the caller has requested data that does not exist.
        """
        pass  # pylint: disable=unnecessary-pass

    def __init__(self, user=None):
        """
        Arguments:
            user (:class:`~User`): An already-loaded django user. If this user matches the username
                supplied to `set_many`, then that will reduce the number of queries made to store
                the user state.
        """
        self.user = user

    def _get_student_modules(self, username, block_keys):
        """
        Retrieve the :class:`~StudentModule`s for the supplied ``username`` and ``block_keys``.

        Arguments:
            username (str): The name of the user to load `StudentModule`s for.
            block_keys (list of :class:`~UsageKey`): The set of XBlocks to load data for.
        """
        course_key_func = attrgetter('course_key')
        by_course = itertools.groupby(
            sorted(block_keys, key=course_key_func),
            course_key_func,
        )

        for course_key, usage_keys in by_course:
            query = StudentModule.objects.chunked_filter(
                'module_state_key__in',
                usage_keys,
                student__username=username,
                course_id=course_key,
            )

            for student_module in query:
                usage_key = student_module.module_state_key.map_into_course(student_module.course_id)
                yield (student_module, usage_key)

    def _nr_attribute_name(self, function_name, stat_name, block_type=None):
        """
        Return an attribute name (string) representing the provided blocks.
        The return value is directly usable for New Relic custom attributes.
        """
        if block_type is None:
            attribute_name_parts = ['xb_user_state', function_name, stat_name]
        else:
            attribute_name_parts = ['xb_user_state', function_name, block_type, stat_name]
        return '.'.join(attribute_name_parts)

    def _nr_stat_accumulate(self, function_name, stat_name, value):
        """
        Accumulate arbitrary NR stats (not specific to block types).
        """
        monitoring_utils.accumulate(
            self._nr_attribute_name(function_name, stat_name),
            value
        )

    def _nr_stat_increment(self, function_name, stat_name, count=1):
        """
        Increment arbitrary NR stats (not specific to block types).
        """
        self._nr_stat_accumulate(function_name, stat_name, count)

    def _nr_block_stat_accumulate(self, function_name, block_type, stat_name, value):
        """
        Accumulate NR stats related to block types.
        """
        monitoring_utils.accumulate(
            self._nr_attribute_name(function_name, stat_name),
            value,
        )
        monitoring_utils.accumulate(
            self._nr_attribute_name(function_name, stat_name, block_type=block_type),
            value,
        )

    def _nr_block_stat_increment(self, function_name, block_type, stat_name, count=1):
        """
        Increment NR stats related to block types.
        """
        self._nr_block_stat_accumulate(function_name, block_type, stat_name, count)

    def get_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for the specified XBlock usages.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys ([UsageKey]): A list of UsageKeys identifying which xblock states to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Yields:
            XBlockUserState tuples for each specified UsageKey in block_keys.
            field_state is a dict mapping field names to values.
        """
        if scope != Scope.user_state:
            raise ValueError(f"Only Scope.user_state is supported, not {scope}")

        total_block_count = 0
        evt_time = time()

        # count how many times this function gets called
        self._nr_stat_increment('get_many', 'calls')

        # keep track of blocks requested
        self._nr_stat_accumulate('get_many', 'blocks_requested', len(block_keys))

        modules = self._get_student_modules(username, block_keys)
        for module, usage_key in modules:
            if module.state is None:
                continue

            state = json.loads(module.state)
            state_length = len(module.state)

            # If the state is the empty dict, then it has been deleted, and so
            # conformant UserStateClients should treat it as if it doesn't exist.
            if state == {}:
                continue

            # collect statistics for custom attribute reporting
            self._nr_block_stat_increment('get_many', usage_key.block_type, 'blocks_out')
            self._nr_block_stat_accumulate('get_many', usage_key.block_type, 'size', state_length)
            total_block_count += 1

            # filter state on fields
            if fields is not None:
                state = {
                    field: state[field]
                    for field in fields
                    if field in state
                }
            yield XBlockUserState(username, usage_key, state, module.modified, scope)

        # The rest of this method exists only to report custom attributes.
        finish_time = time()
        duration = (finish_time - evt_time) * 1000  # milliseconds
        self._nr_stat_accumulate('get_many', 'duration', duration)

    def set_many(self, username, block_keys_to_state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys_to_state (dict): A dict mapping UsageKeys to state dicts.
                Each state dict maps field names to values. These state dicts
                are overlaid over the stored state. To delete fields, use
                :meth:`delete` or :meth:`delete_many`.
            scope (Scope): The scope to load data from
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        # count how many times this function gets called
        self._nr_stat_increment('set_many', 'calls')

        # We do a find_or_create for every block (rather than re-using field objects
        # that were queried in get_many) so that if the score has
        # been changed by some other piece of the code, we don't overwrite
        # that score.
        if self.user is not None and self.user.username == username:
            user = self.user
        else:
            user = User.objects.get(username=username)

        if user.is_anonymous:
            # Anonymous users cannot be persisted to the database, so let's just use
            # what we have.
            return

        evt_time = time()

        for usage_key, state in block_keys_to_state.items():
            try:
                student_module, created = StudentModule.objects.get_or_create(
                    student=user,
                    course_id=usage_key.context_key,
                    module_state_key=usage_key,
                    defaults={
                        'state': json.dumps(state),
                        'module_type': usage_key.block_type,
                    },
                )
            except IntegrityError:
                # PLAT-1109 - Until we switch to read committed, we cannot rely
                # on get_or_create to be able to see rows created in another
                # process. This seems to happen frequently, and ignoring it is the
                # best course of action for now
                log.warning("set_many: IntegrityError for student {} - course_id {} - usage key {}".format(  # noqa: UP032  # pylint: disable=line-too-long
                    user, repr(str(usage_key.context_key)), usage_key
                ))
                return

            num_fields_before = num_fields_after = num_new_fields_set = len(state)
            num_fields_updated = 0  # pylint: disable=unused-variable
            if not created:
                if student_module.state is None:
                    current_state = {}
                else:
                    current_state = json.loads(student_module.state)
                num_fields_before = len(current_state)
                current_state.update(state)
                num_fields_after = len(current_state)
                student_module.state = json.dumps(current_state)
                try:
                    with transaction.atomic():
                        # Updating the object - force_update guarantees no INSERT will occur.
                        student_module.save(force_update=True)
                except IntegrityError:
                    # The UPDATE above failed. Log information - but ignore the error.
                    # See https://openedx.atlassian.net/browse/TNL-5365
                    log.warning("set_many: IntegrityError for student {} - course_id {} - usage key {}".format(  # noqa: UP032  # pylint: disable=line-too-long
                        user, repr(str(usage_key.context_key)), usage_key
                    ))
                    log.warning("set_many: All {} block keys: {}".format(  # noqa: UP032
                        len(block_keys_to_state), list(block_keys_to_state.keys())
                    ))

            # DataDog and New Relic reporting

            # record the size of state modifications
            self._nr_block_stat_accumulate('set_many', usage_key.block_type, 'size', len(student_module.state))

            # Record whether a state row has been created or updated.
            if created:
                self._nr_block_stat_increment('set_many', usage_key.block_type, 'blocks_created')
            else:
                self._nr_block_stat_increment('set_many', usage_key.block_type, 'blocks_updated')

            # Event to record number of new fields set in set/set_many.
            num_new_fields_set = num_fields_after - num_fields_before

            # Event to record number of existing fields updated in set/set_many.
            num_fields_updated = max(0, len(state) - num_new_fields_set)  # noqa: F841

        # Events for the entire set_many call.
        finish_time = time()
        duration = (finish_time - evt_time) * 1000  # milliseconds
        self._nr_stat_accumulate('set_many', 'duration', duration)

    def delete_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a many xblock usages.

        Arguments:
            username: The name of the user whose state should be deleted
            block_keys (list): The UsageKey identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        evt_time = time()  # pylint: disable=unused-variable  # noqa: F841
        student_modules = self._get_student_modules(username, block_keys)
        for student_module, _ in student_modules:
            if fields is None:
                student_module.state = "{}"
            else:
                current_state = json.loads(student_module.state)
                for field in fields:
                    if field in current_state:
                        del current_state[field]

                student_module.state = json.dumps(current_state)

            # We just read this object, so we know that we can do an update
            student_module.save(force_update=True)

        # Event for the entire delete_many call.
        finish_time = time()  # pylint: disable=unused-variable  # noqa: F841

    def get_history(self, username, block_key, scope=Scope.user_state):
        """
        Retrieve history of state changes for a given block for a given
        student.  We don't guarantee that history for many blocks will be fast.

        If the specified block doesn't exist, raise :class:`~DoesNotExist`.

        Arguments:
            username: The name of the user whose history should be retrieved.
            block_key: The key identifying which xblock history to retrieve.
            scope (Scope): The scope to load data from.

        Yields:
            XBlockUserState entries for each modification to the specified XBlock, from latest
            to earliest.
        """

        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        student_modules = list(
            student_module
            for student_module, usage_id
            in self._get_student_modules(username, [block_key])
        )
        if len(student_modules) == 0:
            raise self.DoesNotExist()

        history_entries = BaseStudentModuleHistory.get_history(student_modules)

        # If no history records exist, raise an error
        if not history_entries:
            raise self.DoesNotExist()

        for history_entry in history_entries:
            state = history_entry.state

            # If the state is serialized json, then load it
            if state is not None:
                state = json.loads(state)

            # If the state is empty, then for the purposes of `get_history`, it has been
            # deleted, and so we list that entry as `None`.
            if state == {}:
                state = None

            block_key = history_entry.csm.module_state_key
            block_key = block_key.map_into_course(
                history_entry.csm.course_id
            )

            yield XBlockUserState(username, block_key, state, history_entry.created, scope)

    def iter_all_for_block(self, block_key, scope=Scope.user_state):
        """
        Return an iterator over the data stored in the block (e.g. a problem block).

        You get no ordering guarantees.If you're using this method, you should be running in an
        async task.

        Arguments:
            block_key: an XBlock's locator (e.g. :class:`~BlockUsageLocator`)
            scope (Scope): must be `Scope.user_state`

        Returns:
            an iterator over all data. Each invocation returns the next :class:`~XBlockUserState`
                object, which includes the block's contents.
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        results = StudentModule.objects.order_by('id').filter(module_state_key=block_key).select_related('student')
        p = Paginator(results, settings.USER_STATE_BATCH_SIZE)

        for page_number in p.page_range:
            page = p.page(page_number)

            for sm in page.object_list:
                state = json.loads(sm.state)

                if state == {}:
                    continue

                yield XBlockUserState(sm.student.username, sm.module_state_key, state, sm.modified, scope)

    def iter_all_for_course(self, course_key, block_type=None, scope=Scope.user_state):
        """
        Return an iterator over all data stored in a course's blocks.

        You get no ordering guarantees. If you're using this method, you should be running in an
        async task.

        Arguments:
            course_key: a course locator
            scope (Scope): must be `Scope.user_state`

        Returns:
            an iterator over all data. Each invocation returns the next :class:`~XBlockUserState`
                object, which includes the block's contents.
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        results = StudentModule.objects.order_by('id').filter(course_id=course_key)
        if block_type:
            results = results.filter(module_type=block_type)

        p = Paginator(results, settings.USER_STATE_BATCH_SIZE)

        for page_number in p.page_range:
            page = p.page(page_number)

            for sm in page.object_list:
                state = json.loads(sm.state)

                if state == {}:
                    continue

                yield XBlockUserState(sm.student.username, sm.module_state_key, state, sm.modified, scope)
