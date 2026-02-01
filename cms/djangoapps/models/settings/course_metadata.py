"""
Django module for Course Metadata class -- manages advanced settings and related parameters
"""


import logging
from datetime import datetime

import pytz
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from xblock.fields import Scope

from cms.djangoapps.contentstore import toggles
from common.djangoapps.util.db import MYSQL_MAX_INT, generate_int_id
from common.djangoapps.util.proctoring import requires_escalation_email
from common.djangoapps.xblock_django.models import XBlockStudioConfigurationFlag
from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled
from openedx.core.djangoapps.discussions.config.waffle_utils import legacy_discussion_experience_enabled
from openedx.core.lib.teams_config import CONTENT_GROUPS_FOR_TEAMS, TeamsConfig, TeamsetType
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG
from xmodule.course_block import get_available_providers  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import InvalidProctoringProvider  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID
from xmodule.partitions.partitions_service import get_all_partitions_for_course

LOGGER = logging.getLogger(__name__)


class CourseMetadata:
    '''
    For CRUD operations on metadata fields which do not have specific editors
    on the other pages including any user generated ones.
    The objects have no predefined attrs but instead are obj encodings of the
    editable metadata.
    '''
    # The list of fields that wouldn't be shown in Advanced Settings.
    # Should not be used directly. Instead the get_exclude_list_of_fields method should
    # be used if the field needs to be filtered depending on the feature flag.
    FIELDS_EXCLUDE_LIST = [
        'cohort_config',
        'xml_attributes',
        'start',
        'end',
        'enrollment_start',
        'enrollment_end',
        'certificate_available_date',
        'certificates_display_behavior',
        'tabs',
        'graceperiod',
        'show_timezone',
        'format',
        'graded',
        'hide_from_toc',
        'pdf_textbooks',
        'user_partitions',
        'name',  # from xblock
        'tags',  # from xblock
        'visible_to_staff_only',
        'group_access',
        'pre_requisite_courses',
        'entrance_exam_enabled',
        'entrance_exam_minimum_score_pct',
        'entrance_exam_id',
        'is_entrance_exam',
        'in_entrance_exam',
        'language',
        'certificates',
        'minimum_grade_credit',
        'default_time_limit_minutes',
        'is_proctored_enabled',
        'is_time_limited',
        'is_practice_exam',
        'exam_review_rules',
        'hide_after_due',
        'self_paced',
        'show_correctness',
        'chrome',
        'default_tab',
        'highlights_enabled_for_messaging',
        'is_onboarding_exam',
        'discussions_settings',
        'copied_from_block',
        "upstream",
        "upstream_version",
        "upstream_version_declined",
        "upstream_display_name",
    ]

    @classmethod
    def get_exclude_list_of_fields(cls, course_key):
        """
        Returns a list of fields to exclude from the Studio Advanced settings based on a
        feature flag (i.e. enabled or disabled).
        """
        # Copy the filtered list to avoid permanently changing the class attribute.
        exclude_list = list(cls.FIELDS_EXCLUDE_LIST)

        # Do not show giturl if feature is not enabled.
        if not toggles.EXPORT_GIT.is_enabled():
            exclude_list.append('giturl')

        # Do not show edxnotes if the feature is disabled.
        if not settings.FEATURES.get('ENABLE_EDXNOTES'):
            exclude_list.append('edxnotes')

        # Do not show video auto advance if the feature is disabled
        if not settings.FEATURES.get('ENABLE_OTHER_COURSE_SETTINGS'):
            exclude_list.append('other_course_settings')

        # Do not show video_upload_pipeline if the feature is disabled.
        if not settings.FEATURES.get('ENABLE_VIDEO_UPLOAD_PIPELINE'):
            exclude_list.append('video_upload_pipeline')

        # Do not show video auto advance if the feature is disabled
        if not settings.FEATURES.get('ENABLE_AUTOADVANCE_VIDEOS'):
            exclude_list.append('video_auto_advance')

        # Do not show social sharing url field if the feature is disabled.
        if (not hasattr(settings, 'SOCIAL_SHARING_SETTINGS') or
                not getattr(settings, 'SOCIAL_SHARING_SETTINGS', {}).get("CUSTOM_COURSE_URLS")):
            exclude_list.append('social_sharing_url')

        # Do not show teams configuration if feature is disabled.
        if not settings.FEATURES.get('ENABLE_TEAMS'):
            exclude_list.append('teams_configuration')

        if not settings.FEATURES.get('ENABLE_VIDEO_BUMPER'):
            exclude_list.append('video_bumper')

        # Do not show enable_ccx if feature is not enabled.
        if not settings.FEATURES.get('CUSTOM_COURSES_EDX'):
            exclude_list.append('enable_ccx')
            exclude_list.append('ccx_connector')

        # If the XBlockStudioConfiguration table is not being used, there is no need to
        # display the "Allow Unsupported XBlocks" setting.
        if not XBlockStudioConfigurationFlag.is_enabled():
            exclude_list.append('allow_unsupported_xblocks')

        # Do not show "Course Visibility For Unenrolled Learners" in Studio Advanced Settings
        # if the enable_anonymous_access flag is not enabled
        if not COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(course_key=course_key):
            exclude_list.append('course_visibility')

        # Do not show "Proctoring Exam Escalation Contact" if 'requires_escalation_email'
        # is not set on any of the the proctoring backends.
        escalation_email_required = False
        for provider in get_available_providers():
            if requires_escalation_email(provider):
                escalation_email_required = True
                break
        if not escalation_email_required:
            exclude_list.append("proctoring_escalation_email")

        if not legacy_discussion_experience_enabled(course_key):
            exclude_list.append('discussion_blackouts')
            exclude_list.append('allow_anonymous')
            exclude_list.append('allow_anonymous_to_peers')
            exclude_list.append('discussion_topics')
        return exclude_list

    @classmethod
    def fetch(cls, block, filter_fields=None):
        """
        Fetch the key:value editable course details for the given course from
        persistence and return a CourseMetadata model.
        """
        result = {}
        metadata = cls.fetch_all(block, filter_fields=filter_fields)
        exclude_list_of_fields = cls.get_exclude_list_of_fields(block.id)

        for key, value in metadata.items():
            if key in exclude_list_of_fields:
                continue
            result[key] = value
        return result

    @classmethod
    def fetch_all(cls, block, filter_fields=None):
        """
        Fetches all key:value pairs from persistence and returns a CourseMetadata model.
        """
        result = {}
        for field in block.fields.values():
            if field.scope != Scope.settings:
                continue

            if filter_fields and field.name not in filter_fields:
                continue

            field_help = _(field.help)  # lint-amnesty, pylint: disable=translation-of-non-string
            help_args = field.runtime_options.get('help_format_args')
            if help_args is not None:
                field_help = field_help.format(**help_args)

            result[field.name] = {
                'value': field.read_json(block),
                'display_name': _(field.display_name),  # lint-amnesty, pylint: disable=translation-of-non-string
                'help': field_help,
                'deprecated': field.runtime_options.get('deprecated', False),
                'hide_on_enabled_publisher': field.runtime_options.get('hide_on_enabled_publisher', False)
            }
        return result

    @classmethod
    def update_from_json(cls, block, jsondict, user, filter_tabs=True):
        """
        Decode the json into CourseMetadata and save any changed attrs to the db.

        Ensures none of the fields are in the exclude list.
        """
        exclude_list_of_fields = cls.get_exclude_list_of_fields(block.id)
        # Don't filter on the tab attribute if filter_tabs is False.
        if not filter_tabs:
            exclude_list_of_fields.remove("tabs")

        # Validate the values before actually setting them.
        key_values = {}

        for key, model in jsondict.items():
            # should it be an error if one of the filtered list items is in the payload?
            if key in exclude_list_of_fields:
                continue
            try:
                val = model['value']
                if hasattr(block, key) and getattr(block, key) != val:
                    if key == 'proctoring_provider':
                        key_values[key] = block.fields[key].from_json(val, validate_providers=True)
                    else:
                        key_values[key] = block.fields[key].from_json(val)
            except (TypeError, ValueError) as err:
                raise ValueError(_("Incorrect format for field '{name}'. {detailed_message}").format(  # lint-amnesty, pylint: disable=raise-missing-from
                    name=model['display_name'], detailed_message=str(err)))

        return cls.update_from_dict(key_values, block, user)

    @classmethod
    def validate_and_update_from_json(cls, block, jsondict, user, filter_tabs=True):
        """
        Validate the values in the json dict (validated by xblock fields from_json method)

        If all fields validate, go ahead and update those values on the object and return it without
        persisting it to the DB.
        If not, return the error objects list.

        Returns:
            did_validate: whether values pass validation or not
            errors: list of error objects
            result: the updated course metadata or None if error
        """
        exclude_list_of_fields = cls.get_exclude_list_of_fields(block.id)

        if not filter_tabs:
            exclude_list_of_fields.remove("tabs")

        filtered_dict = {k: v for k, v in jsondict.items() if k not in exclude_list_of_fields}
        did_validate = True
        errors = []
        key_values = {}
        updated_data = None

        for key, model in filtered_dict.items():
            try:
                val = model['value']
                if hasattr(block, key) and getattr(block, key) != val:
                    if key == 'proctoring_provider':
                        key_values[key] = block.fields[key].from_json(val, validate_providers=True)
                    else:
                        key_values[key] = block.fields[key].from_json(val)
            except (TypeError, ValueError, ValidationError) as err:
                did_validate = False
                errors.append({'key': key, 'message': str(err), 'model': model})
            except InvalidProctoringProvider as err:
                # LTI is automatically considered a proctoring provider, so it will be included in the error message
                # Because we cannot pass course context to the exception, we need to check if the LTI provider
                # should actually be available to the course
                err_message = str(err)
                if not exams_ida_enabled(block.id):
                    available_providers = get_available_providers()
                    available_providers.remove('lti_external')
                    err_message = str(InvalidProctoringProvider(val, available_providers))

                did_validate = False
                errors.append({'key': key, 'message': err_message, 'model': model})

        teams_config = key_values.get('teams_configuration')
        if teams_config:
            key_values['teams_configuration'] = cls.fill_teams_user_partitions_ids(block, teams_config)

        team_setting_errors = cls.validate_team_settings(filtered_dict)
        if team_setting_errors:
            errors = errors + team_setting_errors
            did_validate = False

        proctoring_errors = cls.validate_proctoring_settings(block, filtered_dict, user)
        if proctoring_errors:
            errors = errors + proctoring_errors
            did_validate = False

        # If did validate, go ahead and update the metadata
        if did_validate:
            updated_data = cls.update_from_dict(key_values, block, user, save=False)

        return did_validate, errors, updated_data

    @staticmethod
    def get_user_partition_id(block, min_partition_id, max_partition_id):
        """
        Get a dynamic partition id that is not already in use.
        """
        used_partition_ids = {p.id for p in get_all_partitions_for_course(block)}
        return generate_int_id(
            min_partition_id,
            max_partition_id,
            used_partition_ids,
        )

    @classmethod
    def fill_teams_user_partitions_ids(cls, block, teams_config):
        """
        Fill the `user_partition_id` in the team settings if it is not set.

        This is used by the Dynamic Team Partition Generator to create the dynamic user partitions
        based on the team-sets defined in the course.
        """
        if not block.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(block.id):
            return teams_config

        for team_set in teams_config.teamsets:
            if not team_set.user_partition_id:
                team_set.user_partition_id = cls.get_user_partition_id(
                    block,
                    MINIMUM_UNUSED_PARTITION_ID,
                    MYSQL_MAX_INT,
                )
        return TeamsConfig(
            {
                **teams_config.cleaned_data,
                "team_sets": [team_set.cleaned_data for team_set in teams_config.teamsets],
            }
        )

    @classmethod
    def update_from_dict(cls, key_values, block, user, save=True):
        """
        Update metadata from key_values. Saves to modulestore if save is true.
        """
        for key, value in key_values.items():
            setattr(block, key, value)

        if save and key_values:
            modulestore().update_item(block, user.id)

        return cls.fetch(block)

    @classmethod
    def validate_team_settings(cls, settings_dict):
        """
        Validates team settings

        :param settings_dict: json dict containing all advanced settings
        :return: a list of error objects
        """
        errors = []
        teams_configuration_model = settings_dict.get('teams_configuration', {})
        if teams_configuration_model == {}:
            return errors
        json_value = teams_configuration_model.get('value')
        if json_value == '':
            return errors

        proposed_max_team_size = json_value.get('max_team_size')
        if proposed_max_team_size != '' and proposed_max_team_size is not None:
            if proposed_max_team_size <= 0:
                message = 'max_team_size must be greater than zero'
                errors.append({'key': 'teams_configuration', 'message': message, 'model': teams_configuration_model})
            elif proposed_max_team_size > 500:
                message = 'max_team_size cannot be greater than 500'
                errors.append({'key': 'teams_configuration', 'message': message, 'model': teams_configuration_model})

        proposed_topics = json_value.get('topics')

        if proposed_topics is None:
            proposed_teamsets = json_value.get('team_sets')
            if proposed_teamsets is None:
                return errors
            else:
                proposed_topics = proposed_teamsets

        proposed_topic_ids = [proposed_topic['id'] for proposed_topic in proposed_topics]
        proposed_topic_dupe_ids = {x for x in proposed_topic_ids if proposed_topic_ids.count(x) > 1}
        if len(proposed_topic_dupe_ids) > 0:
            message = 'duplicate ids: ' + ','.join(proposed_topic_dupe_ids)
            errors.append({'key': 'teams_configuration', 'message': message, 'model': teams_configuration_model})

        for proposed_topic in proposed_topics:
            topic_validation_errors = cls.validate_single_topic(proposed_topic)
            if topic_validation_errors:
                topic_validation_errors['model'] = teams_configuration_model
                errors.append(topic_validation_errors)

        return errors

    @classmethod
    def validate_single_topic(cls, topic_settings):
        """
        Helper method that validates a single teamset setting.
        The following conditions result in errors:
        > unrecognized extra keys
        > max_team_size <= 0
        > no name, id or description property
        > unrecognized teamset type
        :param topic_settings: the proposed settings being validated
        :return: an error object if error exists, otherwise None
        """
        error_list = []
        valid_teamset_types = [TeamsetType.open.value, TeamsetType.public_managed.value,
                               TeamsetType.private_managed.value, TeamsetType.open_managed.value]
        valid_keys = {
            'id',
            'name',
            'description',
            'max_team_size',
            'type',
            'user_partition_id',
        }
        teamset_type = topic_settings.get('type', {})
        if teamset_type:
            if teamset_type not in valid_teamset_types:
                error_list.append('type ' + teamset_type + " is invalid")
        max_team_size = topic_settings.get('max_team_size', {})
        if max_team_size:
            if max_team_size <= 0:
                error_list.append('max_team_size must be greater than zero')
            elif max_team_size > 500:
                error_list.append('max_team_size cannot be greater than 500')
        teamset_id = topic_settings.get('id', {})
        if not teamset_id:
            error_list.append('id attribute must not be empty')
        teamset_name = topic_settings.get('name', {})
        if not teamset_name:
            error_list.append('name attribute must not be empty')
        teamset_desc = topic_settings.get('description', {})
        if not teamset_desc:
            error_list.append('description attribute must not be empty')

        keys = set(topic_settings.keys())
        key_difference = keys - valid_keys
        if len(key_difference) > 0:
            error_list.append('extra keys: ' + ','.join(key_difference))

        if error_list:
            error = {'key': 'teams_configuration', 'message': ','.join(error_list)}
            return error

        return None

    @classmethod
    def validate_proctoring_settings(cls, block, settings_dict, user):
        """
        Verify proctoring settings

        Returns a list of error objects
        """
        errors = []

        # If the user is not edX staff, the user has requested a change to the proctoring_provider
        # Advanced Setting, and it is after course start, prevent the user from changing the
        # proctoring provider.
        proctoring_provider_model = settings_dict.get('proctoring_provider', {})
        if (
            not user.is_staff and
            cls._has_requested_proctoring_provider_changed(
                block.proctoring_provider, proctoring_provider_model.get('value')
            ) and
            datetime.now(pytz.UTC) > block.start
        ):
            message = (
                'The proctoring provider cannot be modified after a course has started.'
                ' Contact {support_email} for assistance'
            ).format(support_email=settings.PARTNER_SUPPORT_EMAIL or 'support')
            errors.append({'key': 'proctoring_provider', 'message': message, 'model': proctoring_provider_model})

        # check that a user should actually be able to update the provider to lti, which
        # should only be allowed if the exams IDA is enabled for a course
        available_providers = get_available_providers()
        updated_provider = settings_dict.get('proctoring_provider', {}).get('value')
        if updated_provider == 'lti_external' and not exams_ida_enabled(block.id):
            available_providers.remove('lti_external')
            error = InvalidProctoringProvider('lti_external', available_providers)
            errors.append({'key': 'proctoring_provider', 'message': str(error), 'model': proctoring_provider_model})

        enable_proctoring_model = settings_dict.get('enable_proctored_exams')
        if enable_proctoring_model:
            enable_proctoring = enable_proctoring_model.get('value')
        else:
            enable_proctoring = block.enable_proctored_exams

        if enable_proctoring:

            if proctoring_provider_model:
                proctoring_provider = proctoring_provider_model.get('value')
            else:
                proctoring_provider = block.proctoring_provider

            # If the proctoring provider stored in the course block no longer
            # matches the available providers for this instance, show an error
            if proctoring_provider not in available_providers:
                message = (
                    f'The proctoring provider configured for this course, \'{proctoring_provider}\', is not valid.'
                )
                errors.append({
                    'key': 'proctoring_provider',
                    'message': message,
                    'model': proctoring_provider_model
                })

            # Require a valid escalation email if 'requires_escalation_email' is set in the proctoring backend
            escalation_email_model = settings_dict.get('proctoring_escalation_email')
            if escalation_email_model:
                escalation_email = escalation_email_model.get('value')
            else:
                escalation_email = block.proctoring_escalation_email

            missing_escalation_email_msg = 'Provider \'{provider}\' requires an exam escalation contact.'
            if proctoring_provider_model and requires_escalation_email(proctoring_provider):
                if not escalation_email:
                    message = missing_escalation_email_msg.format(provider=proctoring_provider)
                    errors.append({
                        'key': 'proctoring_provider',
                        'message': message,
                        'model': proctoring_provider_model
                    })

            if (
                escalation_email_model and not proctoring_provider_model and
                requires_escalation_email(proctoring_provider)
            ):
                if not escalation_email:
                    message = missing_escalation_email_msg.format(provider=proctoring_provider)
                    errors.append({
                        'key': 'proctoring_escalation_email',
                        'message': message,
                        'model': escalation_email_model
                    })

            # Check that Zendesk field is appropriate for the provider
            zendesk_ticket_model = settings_dict.get('create_zendesk_tickets')
            if zendesk_ticket_model:
                create_zendesk_tickets = zendesk_ticket_model.get('value')
            else:
                create_zendesk_tickets = block.create_zendesk_tickets

            if proctoring_provider == 'software_secure' and not create_zendesk_tickets:
                LOGGER.info(
                    'create_zendesk_tickets set to {ticket_value} but proctoring '
                    'provider is {provider} for course {course_id}. create_zendesk_tickets '
                    'should be updated for this course.'.format(
                        ticket_value=create_zendesk_tickets,
                        provider=proctoring_provider,
                        course_id=block.id
                    )
                )

        return errors

    @staticmethod
    def _has_requested_proctoring_provider_changed(current_provider, requested_provider):
        """
        Return whether the requested proctoring provider is different than the current proctoring provider, indicating
        that the user has requested a change to the proctoring_provider Advanced Setting.
        """
        if requested_provider is None:
            return False
        else:
            return current_provider != requested_provider
