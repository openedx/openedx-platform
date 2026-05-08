"""
Provide django models to back the discussions app
"""
from __future__ import annotations

import logging
from collections import namedtuple
from enum import Enum  # pylint: disable=wrong-import-order
from typing import List, Type, TypeVar  # noqa: UP035

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_mysql.models import ListCharField
from jsonfield import JSONField
from lti_consumer.models import LtiConfiguration
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import LearningContextKeyField, UsageKeyField
from opaque_keys.edx.keys import CourseKey
from simple_history.models import HistoricalRecords

from openedx.core.djangoapps.config_model_utils.models import StackedConfigurationModel
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger(__name__)

ProviderExternalLinks = namedtuple(
    'ProviderExternalLinks',
    ['learn_more', 'configuration', 'general', 'accessibility', 'contact_email']
)


class Provider:
    """
    List of Discussion providers.
    """
    LEGACY = 'legacy'
    ED_DISCUSS = 'ed-discuss'
    INSCRIBE = 'inscribe'
    PIAZZA = 'piazza'
    YELLOWDIG = 'yellowdig'
    OPEN_EDX = 'openedx'


class PostingRestriction(models.TextChoices):
    """
    Discussions Restrictions choices
    """
    ENABLED = 'enabled'
    DISABLED = 'disabled'
    SCHEDULED = 'scheduled'


DEFAULT_CONFIG_ENABLED = True


def get_default_provider_type() -> str:
    """
    Returns the default provider type to use for new courses.
    Returns:
        (str) default provider type to use
    """
    if ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled():
        return Provider.OPEN_EDX
    else:
        return Provider.LEGACY


class Features(Enum):
    """
    Features to be used/mapped in discussion providers
    """

    # Basic Supported Features
    PRIMARY_DISCUSSION_APP_EXPERIENCE = ('primary-discussion-app-experience', 'basic')
    BASIC_CONFIGURATION = ('basic-configuration', 'basic')
    # DISCUSSION_PAGE = ('discussion-page', 'basic')

    # Partially Supported Features
    QUESTION_DISCUSSION_SUPPORT = ('question-discussion-support', 'partial')
    COMMUNITY_TA_SUPPORT = ('community-ta-support', 'partial')
    REPORT_FLAG_CONTENT_TO_MODERATORS = ('report/flag-content-to-moderators', 'partial')
    LTI_ADVANCED_SHARING_MODE = ('lti-advanced-sharing-mode', 'partial')
    AUTOMATIC_LEARNER_ENROLLMENT = ('automatic-learner-enrollment', 'partial')
    ANONYMOUS_POSTING = ('anonymous-posting', 'partial')
    INTERNATIONALIZATION_SUPPORT = ('internationalization-support', 'partial')
    EMAIL_NOTIFICATIONS = ('email-notifications', 'partial')
    WCAG_2_0_SUPPORT = ('wcag-2.0-support', 'partial')
    BLACKOUT_DISCUSSION_DATES = ('blackout-discussion-dates', 'partial')
    # WCAG_2_1 = ('wcag-2.1', 'partial')
    # EMBEDDED_COURSE_SECTIONS = ('embedded-course-sections', 'basic')

    # Fully Supported Features
    COURSE_COHORT_SUPPORT = ('course-cohort-support', 'full')
    RESEARCH_DATA_EVENTS = ('research-data-events', 'full')

    # Commonly Requested Features
    IN_PLATFORM_NOTIFICATIONS = ('in-platform-notifications', 'common')
    DISCUSSION_CONTENT_PROMPTS = ('discussion-content-prompts', 'common')
    GRADED_DISCUSSIONS = ('graded-discussions', 'common')
    DIRECT_MESSAGES_FROM_INSTRUCTORS = ('direct-messages-from-instructors', 'common')
    USER_MENTIONS = ('user-mentions', 'common')

    def __init__(self, feature_id, feature_support_type):
        self.feature_id = feature_id
        self.feature_support_type = feature_support_type

    @property
    def value(self):  # pylint: disable=invalid-overridden-method
        return self.feature_id

    @property
    def support(self):
        return self.feature_support_type


def pii_sharing_required_message(provider_name):
    """
    Build an i18n'ed message stating PII sharing is required for the provider.
    """
    return _(
        '{provider} requires that LTI advanced sharing be enabled for your course,'
        ' as this provider uses email address and username to personalize'
        ' the experience. Please contact {support_contact} to enable this feature.'
    ).format(
        provider=provider_name,
        support_contact=(
            configuration_helpers.get_value(
                'CONTACT_EMAIL',
                getattr(settings, 'CONTACT_EMAIL', _('technical support'))
            )
        )
    )


AVAILABLE_PROVIDER_MAP = {
    Provider.LEGACY: {
        'features': [
            Features.BASIC_CONFIGURATION.value,
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.COMMUNITY_TA_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.AUTOMATIC_LEARNER_ENROLLMENT.value,
            Features.ANONYMOUS_POSTING.value,
            Features.INTERNATIONALIZATION_SUPPORT.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.BLACKOUT_DISCUSSION_DATES.value,
            Features.COURSE_COHORT_SUPPORT.value,
            Features.RESEARCH_DATA_EVENTS.value,
        ],
        'supports_lti': False,
        'external_links': ProviderExternalLinks(
            learn_more='',
            configuration='',
            general='',
            accessibility='',
            contact_email='',
        )._asdict(),
        'messages': [],
        'has_full_support': True,
        'admin_only_config': False,
    },
    Provider.OPEN_EDX: {
        'features': [
            Features.BASIC_CONFIGURATION.value,
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.COMMUNITY_TA_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.AUTOMATIC_LEARNER_ENROLLMENT.value,
            Features.ANONYMOUS_POSTING.value,
            Features.INTERNATIONALIZATION_SUPPORT.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.BLACKOUT_DISCUSSION_DATES.value,
            Features.COURSE_COHORT_SUPPORT.value,
            Features.RESEARCH_DATA_EVENTS.value,
        ],
        'supports_lti': False,
        'external_links': ProviderExternalLinks(
            learn_more='',
            configuration='',
            general='',
            accessibility='',
            contact_email='',
        )._asdict(),
        'messages': [],
        'has_full_support': True,
        'supports_in_context_discussions': True,
        'admin_only_config': False,
    },
    Provider.ED_DISCUSS: {
        'features': [
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.BASIC_CONFIGURATION.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.LTI_ADVANCED_SHARING_MODE.value,
            Features.AUTOMATIC_LEARNER_ENROLLMENT.value,
            Features.ANONYMOUS_POSTING.value,
            Features.INTERNATIONALIZATION_SUPPORT.value,
            Features.EMAIL_NOTIFICATIONS.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.BLACKOUT_DISCUSSION_DATES.value,
            Features.IN_PLATFORM_NOTIFICATIONS.value,
            Features.USER_MENTIONS.value,
        ],
        'supports_lti': True,
        'external_links': ProviderExternalLinks(
            learn_more='',
            configuration='',
            general='https://edstem.org/us/',
            accessibility='',
            contact_email='',
        )._asdict(),
        'messages': [pii_sharing_required_message('Ed Discussion')],
        'has_full_support': False,
        'admin_only_config': True,
    },
    Provider.INSCRIBE: {
        'features': [
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.BASIC_CONFIGURATION.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.COMMUNITY_TA_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.LTI_ADVANCED_SHARING_MODE.value,
            Features.AUTOMATIC_LEARNER_ENROLLMENT.value,
            Features.ANONYMOUS_POSTING.value,
            Features.INTERNATIONALIZATION_SUPPORT.value,
            Features.EMAIL_NOTIFICATIONS.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.RESEARCH_DATA_EVENTS.value,
            Features.IN_PLATFORM_NOTIFICATIONS.value,
            Features.DISCUSSION_CONTENT_PROMPTS.value,
        ],
        'supports_lti': True,
        'external_links': ProviderExternalLinks(
            learn_more='',
            configuration='',
            general='https://www.inscribeapp.com/',
            accessibility='',
            contact_email='',
        )._asdict(),
        'messages': [pii_sharing_required_message('InScribe')],
        'has_full_support': False,
        'admin_only_config': True,
    },
    Provider.PIAZZA: {
        'features': [
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.BASIC_CONFIGURATION.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.COMMUNITY_TA_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.LTI_ADVANCED_SHARING_MODE.value,
            Features.ANONYMOUS_POSTING.value,
            Features.EMAIL_NOTIFICATIONS.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.BLACKOUT_DISCUSSION_DATES.value,
        ],
        'supports_lti': True,
        'external_links': ProviderExternalLinks(
            learn_more='https://piazza.com/product/overview',
            configuration='https://support.piazza.com/support/solutions/articles/48001065447-configure-piazza-within-edx',  # pylint: disable=line-too-long
            general='https://support.piazza.com/',
            accessibility='https://piazza.com/product/accessibility',
            contact_email='team@piazza.com',
        )._asdict(),
        'messages': [],
        'has_full_support': False,
        'admin_only_config': True
    },
    Provider.YELLOWDIG: {
        'features': [
            Features.PRIMARY_DISCUSSION_APP_EXPERIENCE.value,
            Features.BASIC_CONFIGURATION.value,
            Features.QUESTION_DISCUSSION_SUPPORT.value,
            Features.COMMUNITY_TA_SUPPORT.value,
            Features.REPORT_FLAG_CONTENT_TO_MODERATORS.value,
            Features.EMAIL_NOTIFICATIONS.value,
            Features.WCAG_2_0_SUPPORT.value,
            Features.RESEARCH_DATA_EVENTS.value,
            Features.IN_PLATFORM_NOTIFICATIONS.value,
            Features.GRADED_DISCUSSIONS.value,
            Features.DIRECT_MESSAGES_FROM_INSTRUCTORS.value,
            Features.USER_MENTIONS.value,
        ],
        'supports_lti': True,
        'external_links': ProviderExternalLinks(
            learn_more='https://youtu.be/oOcvjjMVFAw',
            configuration='',
            general='https://hubs.ly/H0J5Bn70',
            accessibility='',
            contact_email='learnmore@yellowdig.com',
        )._asdict(),
        'messages': [pii_sharing_required_message('Yellowdig')],
        'has_full_support': False,
        'admin_only_config': True,
    },
}


def get_supported_providers() -> List[str]:  # noqa: UP006
    """
    Return the list of supported discussion providers

    TODO: Load this from entry points?
    """

    return list(AVAILABLE_PROVIDER_MAP.keys())


class ProviderFilter(StackedConfigurationModel):
    """
    Associate allow/deny-lists of discussions providers with courses/orgs

    .. no_pii:
    """

    allow = ListCharField(
        base_field=models.CharField(
            choices=[
                (provider, provider)
                for provider in get_supported_providers()
            ],
            max_length=20,
        ),
        blank=True,
        help_text=_("Comma-separated list of providers to allow, eg: {choices}").format(
            choices=','.join(get_supported_providers()),
        ),
        # max_length = (size * (max_length + len(','))
        # max_length = (   3 * (        20 +       1))
        max_length=63,
        # size = len(get_supported_providers())
        size=3,
        verbose_name=_('Allow List'),
    )
    deny = ListCharField(
        base_field=models.CharField(
            choices=[
                (provider, provider)
                for provider in get_supported_providers()
            ],
            max_length=20,
        ),
        blank=True,
        help_text=_("Comma-separated list of providers to deny, eg: {choices}").format(
            choices=','.join(get_supported_providers()),
        ),
        # max_length = (size * (max_length + len(','))
        # max_length = (   3 * (        20 +       1))
        max_length=63,
        # size = len(get_supported_providers())
        size=3,
        verbose_name=_('Deny List'),
    )

    STACKABLE_FIELDS = (
        'enabled',
        'allow',
        'deny',
    )

    def __str__(self):
        return 'ProviderFilter(org="{org}", course="{course}", allow={allow}, deny={deny})'.format(
            allow=self.allow,
            course=self.course or '',
            deny=self.deny,
            org=self.org or '',
        )

    @property
    def available_providers(self) -> List[str]:  # noqa: UP006
        """
        Return a filtered list of available providers
        """
        _providers = get_supported_providers()
        if self.allow:
            _providers = [
                provider
                for provider in _providers
                if provider in self.allow
            ]
        if self.deny:
            _providers = [
                provider
                for provider in _providers
                if provider not in self.deny
            ]
        return _providers

    @classmethod
    def get_available_providers(cls, course_key: CourseKey) -> List[str]:  # noqa: UP006
        _filter = cls.current(course_key=course_key)
        providers = _filter.available_providers
        return providers


T = TypeVar('T', bound='DiscussionsConfiguration')


class DiscussionsConfiguration(TimeStampedModel):
    """
    Associates a learning context with discussion provider and configuration

    .. no_pii:
    """

    context_key = LearningContextKeyField(
        primary_key=True,
        db_index=True,
        unique=True,
        max_length=255,
        # Translators: A key specifying a course, library, program,
        # website, or some other collection of content where learning
        # happens.
        verbose_name=_("Learning Context Key"),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=_("If disabled, the discussions in the associated learning context/course will be disabled.")
    )
    posting_restrictions = models.CharField(
        max_length=15,
        default=PostingRestriction.DISABLED,
        choices=PostingRestriction.choices,
        help_text=_(
            "The Posting availability in discussions whether it will be enabled, scheduled or indefinitely disabled."
        )
    )
    lti_configuration = models.ForeignKey(
        LtiConfiguration,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text=_("The LTI configuration data for this context/provider."),
    )
    enable_in_context = models.BooleanField(
        default=True,
        help_text=_(
            "If enabled, discussion topics will be created for each non-graded unit in the course. "
            "A UI for discussions will show up with each unit."
        )
    )
    enable_graded_units = models.BooleanField(
        default=False,
        help_text=_("If enabled, discussion topics will be created for graded units as well.")
    )
    unit_level_visibility = models.BooleanField(
        default=True,
        help_text=_("If enabled, discussions will need to be manually enabled for each unit.")
    )
    plugin_configuration = JSONField(
        blank=True,
        default={},
        help_text=_("The plugin configuration data for this context/provider."),
    )
    provider_type = models.CharField(
        blank=False,
        max_length=100,
        verbose_name=_("Discussion provider"),
        help_text=_("The discussion tool/provider's id"),
        default=get_default_provider_type,
    )
    history = HistoricalRecords()

    def clean(self):
        """
        Validate the model.
        Currently, this only support courses, this can be extended
        whenever discussions are available in other contexts
        """
        if not CourseOverview.course_exists(self.context_key):
            raise ValidationError('Context Key should be an existing learning context.')

    def __str__(self):
        return "DiscussionsConfiguration(context_key='{context_key}', provider='{provider}', enabled={enabled})".format(  # noqa: UP032  # pylint: disable=line-too-long
            context_key=self.context_key,
            provider=self.provider_type,
            enabled=self.enabled,
        )

    def supports_in_context_discussions(self):
        """
        Returns is the provider supports in-context discussions
        """
        return AVAILABLE_PROVIDER_MAP.get(self.provider_type, {}).get('supports_in_context_discussions', False)

    def supports(self, feature: str) -> bool:
        """
        Check if the provider supports some feature
        """
        features = AVAILABLE_PROVIDER_MAP.get(self.provider_type)['features'] or []
        has_support = bool(feature in features)
        return has_support

    def supports_lti(self) -> bool:
        """Returns a boolean indicating if the provider supports lti discussion view."""
        return AVAILABLE_PROVIDER_MAP.get(self.provider_type, {}).get('supports_lti', False)

    @classmethod
    def is_enabled(cls, context_key: CourseKey) -> bool:
        """
        Check if there is an active configuration for a given course key

        Default to False, if no configuration exists
        """
        configuration = cls.get(context_key)
        return configuration.enabled

    @classmethod
    def get(cls: Type[T], context_key: CourseKey) -> T:  # noqa: UP006
        """
        Lookup a model by context_key
        """
        try:
            configuration = cls.objects.get(context_key=context_key)
        except cls.DoesNotExist:
            configuration = cls(
                context_key=context_key,
                enabled=DEFAULT_CONFIG_ENABLED,
                provider_type=get_default_provider_type(),
            )
        return configuration

    @property
    def available_providers(self) -> List[str]:  # noqa: UP006
        return ProviderFilter.current(course_key=self.context_key).available_providers

    @classmethod
    def get_available_providers(cls, context_key: CourseKey) -> List[str]:  # noqa: UP006
        return ProviderFilter.current(course_key=context_key).available_providers

    @classmethod
    def lti_discussion_enabled(cls, course_key: CourseKey) -> bool:
        """
        Checks if LTI discussion is enabled for this course.

        Arguments:
            course_key: course locator.
        Returns:
            Boolean indicating weather or not this course has lti discussion enabled.
        """
        discussion_provider = cls.get(course_key)
        return (
            discussion_provider.enabled
            and discussion_provider.supports_lti()
            and discussion_provider.lti_configuration is not None
        )


class DiscussionTopicLink(models.Model):
    """
    A model linking discussion topics ids to the part of a course they are linked to.

    ..no_pii:
    """
    context_key = LearningContextKeyField(
        db_index=True,
        max_length=255,
        # Translators: A key specifying a course, library, program,
        # website, or some other collection of content where learning
        # happens.
        verbose_name=_("Learning Context Key"),
        help_text=_("Context key for context in which this discussion topic exists.")
    )
    usage_key = UsageKeyField(
        db_index=True,
        max_length=255,
        null=True,
        blank=True,
        help_text=_("Usage key for in-context discussion topic. Set to null for course-level topics.")
    )
    title = models.CharField(
        max_length=255,
        help_text=_("Title for discussion topic.")
    )
    group = models.ForeignKey(
        CourseUserGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_("Group for divided discussions.")
    )
    provider_id = models.CharField(
        max_length=32,
        help_text=_("Provider id for discussion provider.")
    )
    external_id = models.CharField(
        db_index=True,
        max_length=255,
        help_text=_("Discussion context ID in external forum provider. e.g. commentable_id for cs_comments_service.")
    )
    enabled_in_context = models.BooleanField(
        default=True,
        help_text=_("Whether this topic should be shown in-context in the course.")
    )
    ordering = models.PositiveIntegerField(
        null=True,
        help_text=_("Ordering of this topic in its learning context"),
    )
    context = models.JSONField(
        default=dict,
        help_text=_("Additional context for this topic, such as its section, and subsection"),
    )

    def __str__(self):
        return (
            f'DiscussionTopicLink('
            f'context_key="{self.context_key}", usage_key="{self.usage_key}", title="{self.title}", '
            f'group={self.group}, provider_id="{self.provider_id}", external_id="{self.external_id}", '
            f'enabled_in_context={self.enabled_in_context}'
            f')'
        )
