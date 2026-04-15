"""
Signal handlers for program enrollments
"""


import logging
from datetime import datetime

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from social_django.models import UserSocialAuth

from common.djangoapps.third_party_auth.models import SAMLProviderConfig
from openedx.core.djangoapps.catalog.utils import get_programs
from openedx.core.djangoapps.user_api.accounts.signals import USER_RETIRE_LMS_MISC

from .api import fetch_program_enrollments_by_student, link_program_enrollment_to_lms_user
from .models import ProgramEnrollment

logger = logging.getLogger(__name__)


@receiver(USER_RETIRE_LMS_MISC)
def _listen_for_lms_retire(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Listener for the USER_RETIRE_LMS_MISC signal, does user retirement
    """
    user = kwargs.get('user')
    ProgramEnrollment.retire_user(user.id)


@receiver(post_save, sender=UserSocialAuth)
def listen_for_social_auth_creation(sender, instance, created, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    Post-save signal that will attempt to link a social auth entry with waiting enrollments
    """
    try:
        matriculate_learner(instance.user, instance.uid)
    except Exception as e:
        logger.warning(
            'Unable to link waiting enrollments for user %s, social auth creation failed: %s',
            instance.user.id,
            e,
        )
        raise


def generate_default_display_name(self):
    """
    Returns a default display name for SamlProviderConfig.
    """
    time = datetime.now().strftime('%M%S')
    return f'{self.slug}-{time}'


@receiver(pre_save, sender=SAMLProviderConfig)
def save_default_display_name(sender, instance, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    Post-save signal that sets default display name if one is not provided
    """
    this_display_name = instance.display_name
    # check if display_name is None, empty, or just spaces
    if not (this_display_name and this_display_name.strip()):
        instance.display_name = generate_default_display_name(instance)


def matriculate_learner(user, uid):
    """
    Update any waiting program enrollments with a user,
    and enroll the user in any waiting course enrollments.

    In most cases this will just short-circuit. Enrollments will only be updated
    for a SAML provider with a linked organization.
    """
    try:
        provider_slug, external_user_key = uid.split(':')
        authorizing_org = SAMLProviderConfig.objects.current_set().get(slug=provider_slug).organization

        if not authorizing_org:
            return
    except (AttributeError, ValueError):
        logger.debug('Ignoring non-saml social auth entry for user=%s', user.id)
        return
    except SAMLProviderConfig.DoesNotExist:
        logger.warning(
            'Got incoming social auth for provider=%s but no such provider exists', provider_slug
        )
        return
    except SAMLProviderConfig.MultipleObjectsReturned:
        logger.warning(
            'Unable to activate waiting enrollments for user=%s.'
            '  Multiple active SAML configurations found for slug=%s. Expected one.',
            user.id,
            provider_slug)
        return

    incomplete_enrollments = fetch_program_enrollments_by_student(
        external_user_key=external_user_key,
        waiting_only=True,
    ).prefetch_related('program_course_enrollments')

    for enrollment in incomplete_enrollments:
        try:
            enrollment_org = get_programs(uuid=enrollment.program_uuid)['authoring_organizations'][0]
            if enrollment_org['key'] != authorizing_org.short_name:
                continue
        except (KeyError, TypeError):
            logger.warning(
                'Failed to complete waiting enrollments for organization=%s.'
                ' No catalog programs with matching authoring_organization exist.',
                authorizing_org.short_name
            )
            continue
        link_program_enrollment_to_lms_user(enrollment, user)
