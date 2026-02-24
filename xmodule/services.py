"""
Module contains various XModule/XBlock services
"""


import inspect
import logging
from functools import partial
from typing import TYPE_CHECKING

from config_models.models import ConfigurationModel
from django.conf import settings
from django.urls import reverse
from eventtracking import tracker
from edx_when.field_data import DateLookupFieldData
from requests.auth import HTTPBasicAuth
from xblock.reference.plugins import Service
from xblock.runtime import KvsFieldData

from common.djangoapps.track import contexts
from lms.djangoapps.courseware.masquerade import is_masquerading_as_specific_student
from xmodule.modulestore.django import modulestore

from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from lms.djangoapps.courseware.model_data import DjangoKeyValueStore, FieldDataCache
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from lms.djangoapps.lms_xblock.models import XBlockAsidesConfig
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from xmodule.capa.xqueue_interface import XQueueInterface

from lms.djangoapps.grades.api import signals as grades_signals

if TYPE_CHECKING:
    from xmodule.capa_block import ProblemBlock

log = logging.getLogger(__name__)


class SettingsService:
    """
    Allows server-wide configuration of XBlocks on a per-type basis

    XBlock settings are read from XBLOCK_SETTINGS settings key. Each XBlock is allowed access
    to single settings bucket. Bucket is determined by this service using the following rules:

    * Value of SettingsService.xblock_settings_bucket_selector is examined. If XBlock have attribute/property
    with the name of that value this attribute/property is read to get the bucket key (e.g. if XBlock have
    `block_settings_key = 'my_block_settings'`, bucket key would be 'my_block_settings').
    * Otherwise, XBlock class name is used

    Service is content-agnostic: it just returns whatever happen to be in the settings bucket (technically, it returns
    the bucket itself).

    If `default` argument is specified it is returned if:
    * There are no XBLOCK_SETTINGS setting
    * XBLOCK_SETTINGS is empty
    * XBLOCK_SETTINGS does not contain settings bucket

    If `default` is not specified or None, empty dictionary is used for default.

    Example:

        "XBLOCK_SETTINGS": {
            "my_block": {
                "setting1": 1,
                "setting2": []
            },
            "my_other_block": [1, 2, 3],
            "MyThirdBlock": "QWERTY"
        }

        class MyBlock:      block_settings_key='my_block'
        class MyOtherBlock: block_settings_key='my_other_block'
        class MyThirdBlock: pass
        class MissingBlock: pass

        service = SettingsService()
        service.get_settings_bucket(MyBlock())                      # { "setting1": 1, "setting2": [] }
        service.get_settings_bucket(MyOtherBlock())                 # [1, 2, 3]
        service.get_settings_bucket(MyThirdBlock())                 # "QWERTY"
        service.get_settings_bucket(MissingBlock())                 # {}
        service.get_settings_bucket(MissingBlock(), "default")      # "default"
        service.get_settings_bucket(MissingBlock(), None)           # {}
    """
    xblock_settings_bucket_selector = 'block_settings_key'

    def get_settings_bucket(self, block, default=None):
        """ Gets xblock settings dictionary from settings. """
        if not block:
            raise ValueError(f"Expected XBlock instance, got {block} of type {type(block)}")

        actual_default = default if default is not None else {}
        xblock_settings_bucket = getattr(block, self.xblock_settings_bucket_selector, block.unmixed_class.__name__)
        xblock_settings = settings.XBLOCK_SETTINGS if hasattr(settings, "XBLOCK_SETTINGS") else {}
        return xblock_settings.get(xblock_settings_bucket, actual_default)


# TODO: ConfigurationService and its usage will be removed as a part of EDUCATOR-121
# reference: https://openedx.atlassian.net/browse/EDUCATOR-121
class ConfigurationService:
    """
    An XBlock service to talk with the Configuration Models. This service should provide
    a pathway to Configuration Model which is designed to configure the corresponding XBlock.
    """
    def __init__(self, configuration_model):
        """
        Class initializer, this exposes configuration model to XBlock.

        Arguments:
            configuration_model (ConfigurationModel): configurations for an XBlock

        Raises:
            exception (ValueError): when configuration_model is not a subclass of
            ConfigurationModel.
        """
        if not (inspect.isclass(configuration_model) and issubclass(configuration_model, ConfigurationModel)):
            raise ValueError(
                "Expected ConfigurationModel got {} of type {}".format(
                    configuration_model,
                    type(configuration_model)
                )
            )

        self.configuration = configuration_model


class TeamsConfigurationService:
    """
    An XBlock service that returns the teams_configuration object for a course.
    """
    def __init__(self):
        self._course = None

    def get_course(self, course_id):
        """
        Return the course instance associated with this TeamsConfigurationService.
        This default implementation looks up the course from the modulestore.
        """
        return modulestore().get_course(course_id)

    def get_teams_configuration(self, course_id):
        """
        Returns the team configuration for a given course.id
        """
        if not self._course:
            self._course = self.get_course(course_id)
        return self._course.teams_configuration


class RebindUserServiceError(Exception):
    pass


class RebindUserService(Service):
    """
    An XBlock Service that allows modules to get rebound to real users if it was previously bound to an AnonymousUser.

    This used to be a local function inside the `lms.djangoapps.courseware.block_render.prepare_runtime_for_user`
    method. This has been refactored out into a service and lives in this module temporarily.

    TODO: Only the old LTI XBlock uses it in 2 places for LTI 2.0 integration. As the LTI XBlock is deprecated in
    favour of the LTI Consumer XBlock, this should be removed when the LTI XBlock is removed.

    Arguments:
        user (User) - A Django User object
        course_id (str) - Course ID
        course (Course) - Course Object
        kwargs (dict) - all the keyword arguments that need to be passed to the `prepare_runtime_for_user`
            function when it is called during rebinding
    """
    def __init__(self, user, course_id, **kwargs):
        super().__init__(**kwargs)
        self.user = user
        self.course_id = course_id
        self._kwargs = kwargs

    def rebind_noauth_module_to_user(self, block, real_user):
        """
        Function that rebinds the module to the real_user.

        Will only work within a module bound to an AnonymousUser, e.g. one that's instantiated by the noauth_handler.

        Arguments:
            block (any xblock type):  the module to rebind
            real_user (django.contrib.auth.models.User):  the user to bind to

        Returns:
            nothing (but the side effect is that module is re-bound to real_user)
        """
        if self.user.is_authenticated:
            err_msg = "rebind_noauth_module_to_user can only be called from a module bound to an anonymous user"
            log.error(err_msg)
            raise RebindUserServiceError(err_msg)

        field_data_cache_real_user = FieldDataCache.cache_for_block_descendents(
            self.course_id,
            real_user,
            block,
            asides=XBlockAsidesConfig.possible_asides(),
        )
        student_data_real_user = KvsFieldData(DjangoKeyValueStore(field_data_cache_real_user))

        with modulestore().bulk_operations(self.course_id):
            course = modulestore().get_course(course_key=self.course_id)

        from lms.djangoapps.courseware.block_render import prepare_runtime_for_user
        prepare_runtime_for_user(
            user=real_user,
            student_data=student_data_real_user,  # These have implicit user bindings, rest of args considered not to
            runtime=block.runtime,
            course_id=self.course_id,
            course=course,
            **self._kwargs
        )

        block.bind_for_student(
            real_user.id,
            [
                partial(DateLookupFieldData, course_id=self.course_id, user=self.user),
                partial(OverrideFieldData.wrap, real_user, course),
                partial(LmsFieldData, student_data=student_data_real_user),
            ],
        )


class EventPublishingService(Service):
    """
    An XBlock Service that allows XModules to publish events (e.g. grading, completion).

    We have implemented it as a seperate service to be able to alter its behavior when using
    a different context: LMS, Studio, or Instructor tasks.
    """
    def __init__(self, user, course_id, track_function, **kwargs):
        super().__init__(**kwargs)
        self.user = user
        self.course_id = course_id
        self.track_function = track_function
        self.completion_service = None

    def publish(self, block, event_type, event):
        """
        A function that allows XModules to publish events.
        """
        self.completion_service = block.runtime.service(block, 'completion')

        handle_event = self._get_event_handler(event_type)
        if handle_event and not is_masquerading_as_specific_student(self.user, self.course_id):
            handle_event(block, event)
        else:
            context = contexts.course_context_from_course_id(self.course_id)
            if not self.user.is_anonymous:
                context['user_id'] = self.user.id

            context['asides'] = {}
            for aside in block.runtime.get_asides(block):
                if hasattr(aside, 'get_event_context'):
                    aside_event_info = aside.get_event_context(event_type, event)
                    if aside_event_info is not None:
                        context['asides'][aside.scope_ids.block_type] = aside_event_info
            with tracker.get_tracker().context(event_type, context):
                self.track_function(event_type, event)

    def _get_event_handler(self, event_type):
        """
        Return an appropriate function to handle the event.

        Returns None if no special processing is required.
        """
        handlers = {
            'grade': self._handle_grade_event,
        }
        if self.completion_service and self.completion_service.completion_tracking_enabled():
            handlers.update(
                {
                    'completion': lambda block, event: self.completion_service.submit_completion(
                        block.scope_ids.usage_id, event['completion']
                    ),
                    'progress': self._handle_deprecated_progress_event,
                }
            )
        return handlers.get(event_type)

    def _handle_grade_event(self, block, event):
        """
        Submit a grade for the block.
        """
        if not self.user.is_anonymous:
            grades_signals.SCORE_PUBLISHED.send(
                sender=None,
                block=block,
                user=self.user,
                raw_earned=event['value'],
                raw_possible=event['max_value'],
                only_if_higher=event.get('only_if_higher'),
                score_deleted=event.get('score_deleted'),
                grader_response=event.get('grader_response'),
            )

    def _handle_deprecated_progress_event(self, block, event):
        """
        DEPRECATED: Submit a completion for the block represented by the
        progress event.

        This exists to support the legacy progress extension used by
        edx-solutions.  New XBlocks should not emit these events, but instead
        emit completion events directly.
        """
        requested_user_id = event.get('user_id', self.user.id)
        if requested_user_id != self.user.id:
            log.warning(f"{self.user} tried to submit a completion on behalf of {requested_user_id}")
            return

        # If blocks explicitly declare support for the new completion API,
        # we expect them to emit 'completion' events,
        # and we ignore the deprecated 'progress' events
        # in order to avoid duplicate work and possibly conflicting semantics.
        if not getattr(block, 'has_custom_completion', False):
            self.completion_service.submit_completion(block.scope_ids.usage_id, 1.0)


# .. toggle_name: send_to_submission_course.enable
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_description: Enables use of the submissions service instead of legacy xqueue for course problem submissions.
# .. toggle_default: False
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2024-04-03
# .. toggle_expiration_date: 2025-08-12
# .. toggle_will_remain_in_codebase: True
# .. toggle_tickets: none
# .. toggle_status: supported
SEND_TO_SUBMISSION_COURSE_FLAG = CourseWaffleFlag("send_to_submission_course.enable", __name__)


class XQueueService:
    """
    XBlock service providing an interface to the XQueue service.

    Args:
        block: The `ProblemBlock` instance.
    """

    def __init__(self, block: "ProblemBlock"):
        self._block = block

        basic_auth = settings.XQUEUE_INTERFACE.get("basic_auth")
        requests_auth = HTTPBasicAuth(*basic_auth) if basic_auth else None

        use_submission = self.use_edx_submissions_for_xqueue()

        self._interface = XQueueInterface(
            settings.XQUEUE_INTERFACE["url"],
            settings.XQUEUE_INTERFACE["django_auth"],
            requests_auth,
            block=block,
            use_submission_service=use_submission,
        )

    @property
    def interface(self):
        """
        Returns the XQueueInterface instance.
        """
        return self._interface

    def use_edx_submissions_for_xqueue(self) -> bool:
        """
        Determines whether edx-submissions should be used instead of legacy XQueue.

        This helper abstracts the toggle logic so that the rest of the codebase is not tied
        to specific feature flag mechanics or rollout strategies.

        Returns:
            bool: True if edx-submissions should be used, False otherwise.
        """
        return SEND_TO_SUBMISSION_COURSE_FLAG.is_enabled(self._block.scope_ids.usage_id.context_key)

    def construct_callback(self, dispatch: str = "score_update") -> str:
        """
        Return a fully qualified callback URL for the external queueing system.
        """
        course_key = self._block.scope_ids.usage_id.context_key
        userid = str(self._block.scope_ids.user_id)
        mod_id = str(self._block.scope_ids.usage_id)

        callback_type = "xqueue_callback"

        relative_xqueue_callback_url = reverse(
            callback_type,
            kwargs={
                "course_id": str(course_key),
                "userid": userid,
                "mod_id": mod_id,
                "dispatch": dispatch,
            },
        )

        xqueue_callback_url_prefix = settings.XQUEUE_INTERFACE.get("callback_url", settings.LMS_ROOT_URL)
        return f"{xqueue_callback_url_prefix}{relative_xqueue_callback_url}"

    @property
    def default_queuename(self) -> str:
        """
        Returns the default queue name for the current course.
        """
        course_id = self._block.scope_ids.usage_id.context_key
        return f"{course_id.org}-{course_id.course}".replace(" ", "_")

    @property
    def waittime(self) -> int:
        """
        Returns the number of seconds to wait in between calls to XQueue.
        """
        return settings.XQUEUE_WAITTIME_BETWEEN_REQUESTS
