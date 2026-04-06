"""Models for API management."""


import logging
from smtplib import SMTPException
from urllib.parse import urlunsplit

from config_models.models import ConfigurationModel
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext as _u
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel

from common.djangoapps.edxmako.shortcuts import render_to_string
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger(__name__)


class ApiAccessRequest(TimeStampedModel):
    """
    Model to track API access for a user.

    .. pii: Stores a website, company name, company address for this user
    .. pii_types: location, external_service, other
    .. pii_retirement: local_api
    """

    PENDING = 'pending'
    DENIED = 'denied'
    APPROVED = 'approved'
    STATUS_CHOICES = (
        (PENDING, _('Pending')),
        (DENIED, _('Denied')),
        (APPROVED, _('Approved')),
    )
    user = models.OneToOneField(User, related_name='api_access_request', on_delete=models.CASCADE)
    status = models.CharField(
        max_length=255,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True,
        help_text=_('Status of this API access request'),
    )
    website = models.URLField(help_text=_('The URL of the website associated with this API user.'))
    reason = models.TextField(help_text=_('The reason this user wants to access the API.'))
    company_name = models.CharField(max_length=255, default='')
    company_address = models.CharField(max_length=255, default='')
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    contacted = models.BooleanField(default=False)

    class Meta:
        get_latest_by = 'modified'
        ordering = ('-modified', '-created',)

    @classmethod
    def has_api_access(cls, user):
        """Returns whether or not this user has been granted API access.

        Arguments:
            user (User): The user to check access for.

        Returns:
            bool
        """
        return cls.api_access_status(user) == cls.APPROVED

    @classmethod
    def api_access_status(cls, user):
        """
        Returns the user's API access status, or None if they have not
        requested access.

        Arguments:
            user (User): The user to check access for.

        Returns:
            str or None
        """
        try:
            return cls.objects.get(user=user).status
        except cls.DoesNotExist:
            return None

    @classmethod
    def retire_user(cls, user):
        """
        Retires the user's API acccess request table for GDPR

        Arguments:
            user (User): The user linked to the data to retire in the model.

        Returns:
            True: If the user has a linked data in the model and retirement is successful
            False: user has no linked data in the model.
        """
        try:
            retire_target = cls.objects.get(user=user)
        except cls.DoesNotExist:
            return False
        else:
            retire_target.website = ''
            retire_target.company_address = ''
            retire_target.company_name = ''
            retire_target.reason = ''
            retire_target.save()
            return True

    def approve(self):
        """Approve this request."""
        log.info('Approving API request from user [%s].', self.user.id)
        self.status = self.APPROVED
        self.save()

    def deny(self):
        """Deny this request."""
        log.info('Denying API request from user [%s].', self.user.id)
        self.status = self.DENIED
        self.save()

    def __str__(self):
        return f'ApiAccessRequest {self.website} [{self.status}]'


class ApiAccessConfig(ConfigurationModel):
    """
    Configuration for API management.

    .. no_pii:
    """

    def __str__(self):
        return f'ApiAccessConfig [enabled={self.enabled}]'


@receiver(post_save, sender=ApiAccessRequest, dispatch_uid="api_access_request_post_save_email")
def send_request_email(sender, instance, created, **kwargs):   # pylint: disable=unused-argument
    """ Send request email after new record created. """
    if created:
        _send_new_pending_email(instance)


@receiver(pre_save, sender=ApiAccessRequest, dispatch_uid="api_access_request_pre_save_email")
def send_decision_email(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """ Send decision email after status changed. """
    if instance.id and not instance.contacted:
        old_instance = ApiAccessRequest.objects.get(pk=instance.id)
        if instance.status != old_instance.status:
            _send_decision_email(instance)


def _send_new_pending_email(instance):
    """ Send an email to settings.API_ACCESS_MANAGER_EMAIL with the contents of this API access request. """
    context = {
        'approval_url': urlunsplit(
            (
                'https' if settings.HTTPS == 'on' else 'http',
                instance.site.domain,
                reverse('admin:api_admin_apiaccessrequest_change', args=(instance.id,)),
                '',
                '',
            )
        ),
        'api_request': instance
    }

    message = render_to_string('api_admin/api_access_request_email_new_request.txt', context)
    try:
        send_mail(
            _u('API access request from {company}').format(company=instance.company_name),
            message,
            settings.API_ACCESS_FROM_EMAIL,
            [settings.API_ACCESS_MANAGER_EMAIL],
            fail_silently=False
        )
    except SMTPException:
        log.exception('Error sending API user notification email for request [%s].', instance.id)


def _send_decision_email(instance):
    """ Send an email to requesting user with the decision made about their request. """
    context = {
        'name': instance.user.username,
        'api_management_url': urlunsplit(
            (
                'https' if settings.HTTPS == 'on' else 'http',
                instance.site.domain,
                reverse('api_admin:api-status'),
                '',
                '',
            )
        ),
        'authentication_docs_url': settings.AUTH_DOCUMENTATION_URL,
        'api_docs_url': settings.API_DOCUMENTATION_URL,
        'support_email_address': settings.API_ACCESS_FROM_EMAIL,
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
    }

    message = render_to_string(
        f'api_admin/api_access_request_email_{instance.status}.txt',
        context
    )
    try:
        send_mail(
            _u('API access request'),
            message,
            settings.API_ACCESS_FROM_EMAIL,
            [instance.user.email],
            fail_silently=False
        )
        instance.contacted = True
    except SMTPException:
        log.exception('Error sending API user notification email for request [%s].', instance.id)


class Catalog(models.Model):
    """
    A (non-Django-managed) model for Catalogs in the course discovery service.

    .. no_pii:
    """

    id = models.IntegerField(primary_key=True)  # pylint: disable=invalid-name
    name = models.CharField(max_length=255, null=False, blank=False)
    query = models.TextField(null=False, blank=False)
    viewers = models.TextField()

    class Meta:
        # Catalogs live in course discovery, so we do not create any
        # tables in LMS. Instead we override the save method to not
        # touch the database, and use our API client to communicate
        # with discovery.
        managed = False

    def __init__(self, *args, **kwargs):
        attributes = kwargs.get('attributes')
        if attributes:
            self.id = attributes['id']  # pylint: disable=invalid-name
            self.name = attributes['name']
            self.query = attributes['query']
            self.viewers = attributes['viewers']
        else:
            super().__init__(*args, **kwargs)

    def save(self, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ, unused-argument
        return None

    @property
    def attributes(self):
        """Return a dictionary representation of this catalog."""
        return {
            'id': self.id,
            'name': self.name,
            'query': self.query,
            'viewers': self.viewers,
        }

    def __str__(self):
        return f'Catalog {self.name} [{self.query}]'
