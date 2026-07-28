"""
Defines asynchronous celery task for auto-tagging content
"""
from __future__ import annotations

import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.conf import settings
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryUsageLocatorV2
from openedx_tagging.models import Taxonomy

from xmodule.modulestore.django import modulestore

from . import api
from .types import ContentKey

LANGUAGE_TAXONOMY_ID = -1

log = logging.getLogger(__name__)
User = get_user_model()


def _set_initial_language_tag(content_key: ContentKey, lang_code: str) -> None:
    """
    Create a tag for the language taxonomy in the content_object if it doesn't exist.

    lang_code is the two-letter language code, optionally with country suffix.

    If the language is not configured in the plataform or the language tag doesn't exist,
    use the default language of the platform.
    """
    lang_taxonomy = Taxonomy.objects.get(pk=LANGUAGE_TAXONOMY_ID).cast()

    if lang_code and not api.get_object_tags(object_id=str(content_key), taxonomy_id=lang_taxonomy.id):
        try:
            lang_tag = lang_taxonomy.tag_for_external_id(lang_code)
        except api.oel_tagging.TagDoesNotExist:
            default_lang_code = settings.LANGUAGE_CODE
            logging.warning(
                "Language not configured in the plataform: %s. Using default language: %s",
                lang_code,
                default_lang_code,
            )
            lang_tag = lang_taxonomy.tag_for_external_id(default_lang_code)
        api.tag_object(
            object_id=str(content_key),
            taxonomy=lang_taxonomy,
            tags=[lang_tag.value],
        )


def _delete_tags(content_object: ContentKey) -> None:
    api.delete_object_tags(str(content_object))


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def update_course_tags(course_key_str: str) -> bool:
    """
    Updates the automatically-managed tags for a course
    (whenever a course is created or updated)

    Params:
        course_key_str (str): identifier of the Course
    """
    try:
        course_key = CourseKey.from_string(course_key_str)

        log.info("Updating tags for Course with id: %s", course_key)

        course = modulestore().get_course(course_key)
        if course:
            lang_code = course.language
            _set_initial_language_tag(course_key, lang_code)

        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error updating tags for Course with id: %s. %s", course_key, e)
        return False


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def delete_course_tags(course_key_str: str) -> bool:
    """
    Delete the tags for a Course (when the course itself has been deleted).

    Params:
        course_key_str (str): identifier of the Course
    """
    try:
        course_key = CourseKey.from_string(course_key_str)

        log.info("Deleting tags for Course with id: %s", course_key)

        _delete_tags(course_key)

        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error deleting tags for Course with id: %s. %s", course_key, e)
        return False


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def update_xblock_tags(usage_key_str: str) -> bool:
    """
    Updates the automatically-managed tags for a XBlock
    (whenever an XBlock is created/updated).

    Params:
        usage_key_str (str): identifier of the XBlock
    """
    try:
        usage_key = UsageKey.from_string(usage_key_str)

        log.info("Updating tags for XBlock with id: %s", usage_key)

        if usage_key.course_key.is_course:
            course = modulestore().get_course(usage_key.course_key)
            if course is None:
                return True
            lang_code = course.language
        else:
            return True

        _set_initial_language_tag(usage_key, lang_code)

        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error updating tags for XBlock with id: %s. %s", usage_key, e)
        return False


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def delete_xblock_tags(usage_key_str: str) -> bool:
    """
    Delete the tags for a XBlock (when the XBlock itself is deleted).

    Params:
        usage_key_str (str): identifier of the XBlock
    """
    try:
        usage_key = UsageKey.from_string(usage_key_str)

        log.info("Deleting tags for XBlock with id: %s", usage_key)

        _delete_tags(usage_key)

        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error deleting tags for XBlock with id: %s. %s", usage_key, e)
        return False


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def update_library_block_tags(usage_key_str: str, language_code: str) -> bool:
    """
    Updates the automatically-managed tags for a content library block
    whenever it is created/updated

    Params:
        usage_key_str (str): identifier of the Library Block
        langauge_code (str): the preferred language code of the user
    """
    try:
        usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

        log.info("Updating tags for Library Block with id: %s", usage_key)

        _set_initial_language_tag(usage_key, language_code)
        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error updating tags for XBlock with id: %s. %s", usage_key, e)
        return False


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def delete_library_block_tags(usage_key_str: str) -> bool:
    """
    Delete the tags for a Library Block (when the Library Block itself is deleted).

    Params:
        usage_key_str (str): identifier of the Library Block
    """
    try:
        usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

        log.info("Deleting tags for Library Block with id: %s", usage_key)

        _delete_tags(usage_key)

        return True
    except Exception as e:  # pylint: disable=broad-except
        log.error("Error deleting tags for Library Block with id: %s. %s", usage_key, e)
        return False
