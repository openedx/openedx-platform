"""
Specialized models for oauth_dispatch djangoapp
"""


from datetime import datetime

from django.db import models

from django.utils.translation import gettext_lazy as _
from django_mysql.models import ListCharField
from oauth2_provider.settings import oauth2_settings
from organizations.models import Organization
from zoneinfo import ZoneInfo

from openedx.core.djangolib.markup import HTML
from openedx.core.lib.request_utils import get_request_or_stub


class RestrictedApplication(models.Model):
    """
    This model lists which django-oauth-toolkit Applications are considered 'restricted'
    and thus have a limited ability to use various APIs.

    A restricted Application will only get expired token/JWT payloads
    so that they cannot be used to call into APIs.

    .. no_pii:
    """

    application = models.ForeignKey(oauth2_settings.APPLICATION_MODEL, null=False, on_delete=models.CASCADE)

    class Meta:
        app_label = 'oauth_dispatch'

    def __str__(self):  # lint-amnesty, pylint: disable=invalid-str-returned
        """
        Return a unicode representation of this object
        """
        return HTML("<RestrictedApplication '{name}'>").format(
            name=HTML(self.application.name)
        )

    @classmethod
    def should_expire_access_token(cls, application):
        jwt_not_requested = get_request_or_stub().POST.get('token_type', '').lower() != 'jwt'
        restricted_application = cls.objects.filter(application=application).exists()
        return restricted_application and jwt_not_requested

    @classmethod
    def verify_access_token_as_expired(cls, access_token):
        """
        For access_tokens for RestrictedApplications, make sure that the expiry date
        is set at the beginning of the epoch which is Jan. 1, 1970
        """
        return access_token.expires == datetime(1970, 1, 1, tzinfo=ZoneInfo("UTC"))


class ApplicationAccess(models.Model):
    """
    Specifies access control information for the associated Application.

    For usage details, see:
    - openedx/core/djangoapps/oauth_dispatch/docs/decisions/0007-include-organizations-in-tokens.rst

    .. no_pii:
    """

    # Content org filters are of the form "content_org:<org_name>" eg. "content_org:SchoolX"
    # and indicate that for anything that cares about the content_org filter, that the response
    # should be filtered based on the filter value.  ie. We should only get responses pertain
    # to objects that are relevant to the SchoolX organization.
    CONTENT_ORG_FILTER_NAME = 'content_org'

    application = models.OneToOneField(oauth2_settings.APPLICATION_MODEL, related_name='access',
                                       on_delete=models.CASCADE)
    scopes = ListCharField(
        base_field=models.CharField(max_length=32),
        size=25,
        max_length=(25 * 33),  # 25 * 32 character scopes, plus commas
        help_text=_('Comma-separated list of scopes that this application will be allowed to request.'),
    )

    filters = ListCharField(
        base_field=models.CharField(max_length=32),
        size=25,
        max_length=(25 * 33),  # 25 * 32 character filters, plus commas
        help_text=_('Comma-separated list of filters that this application will be allowed to request.'),
        null=True,
        blank=True,
    )

    class Meta:
        app_label = 'oauth_dispatch'

    @classmethod
    def get_scopes(cls, application):
        return cls.objects.get(application=application).scopes

    @classmethod
    def get_filters(cls, application):
        return cls.objects.get(application=application).filters

    @classmethod
    def get_filter_values(cls, application, filter_name):  # lint-amnesty, pylint: disable=missing-function-docstring
        filters = cls.get_filters(application=application)
        if filters:
            for filter_constraint in filters:
                name, filter_value = filter_constraint.split(':', 1)
                if name == filter_name:
                    yield filter_value

    def __str__(self):
        """
        Return a unicode representation of this object.
        """
        return "{application_name}:{scopes}:{filters}".format(
            application_name=self.application.name,
            scopes=self.scopes,
            filters=self.filters,
        )


class ApplicationOrganization(models.Model):
    """
    DEPRECATED: Associates a DOT Application to an Organization.

    This model is no longer in use.

    TODO: BOM-1270: This model and table will be removed post-Juniper
    so Open edX instances can migrate data if necessary.

    To migrate, use ApplicationAccess and add a ``filter`` of the form
    ``content_org:<ORG NAME>`` (e.g. content_org:edx), for each record
    in this model's table.

    .. no_pii:
    """
    RELATION_TYPE_CONTENT_ORG = 'content_org'
    RELATION_TYPES = (
        (RELATION_TYPE_CONTENT_ORG, _('Content Provider')),
    )

    application = models.ForeignKey(oauth2_settings.APPLICATION_MODEL, related_name='organizations',
                                    on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    relation_type = models.CharField(
        max_length=32,
        choices=RELATION_TYPES,
        default=RELATION_TYPE_CONTENT_ORG,
    )

    class Meta:
        app_label = 'oauth_dispatch'
        unique_together = ('application', 'relation_type', 'organization')
