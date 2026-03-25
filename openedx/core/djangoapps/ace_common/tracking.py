# pylint: disable=missing-docstring

from urllib.parse import parse_qs  # noqa: I001

import attr
from django.utils.http import urlencode

from openedx.core.djangoapps.theming.helpers import get_config_value_from_site_or_settings

DEFAULT_CAMPAIGN_SOURCE = 'ace'
DEFAULT_CAMPAIGN_MEDIUM = 'email'


@attr.s
class CampaignTrackingInfo:
    """
    A struct for storing the set of UTM parameters that are recognized by tracking tools when included in URLs.
    """
    source = attr.ib(default=DEFAULT_CAMPAIGN_SOURCE)
    medium = attr.ib(default=DEFAULT_CAMPAIGN_MEDIUM)
    campaign = attr.ib(default=None)
    term = attr.ib(default=None)
    content = attr.ib(default=None)

    def to_query_string(self, existing_query_string=None):
        """
        Generate a query string that includes the tracking parameters in addition to any existing parameters.

        Note that any existing UTM parameters will be overridden by the values in this instance of CampaignTrackingInfo.

        Args:
            existing_query_string (str): An existing query string that needs to be updated to include this tracking
                information.

        Returns:
            str: The URL encoded string that should be used as the query string in the URL.
        """
        parameters = {}
        if existing_query_string is not None:
            parameters = parse_qs(existing_query_string)

        for attribute, value in attr.asdict(self).items():
            if value is not None:
                parameters['utm_' + attribute] = [value]
        return urlencode(parameters, doseq=True)


@attr.s
class GoogleAnalyticsTrackingPixel:
    """
    Implementation of the Google Analytics measurement protocol for email tracking.

    See this document for more info: https://developers.google.com/analytics/devguides/collection/protocol/v1/email
    """
    ANONYMOUS_USER_CLIENT_ID = 555

    site = attr.ib(default=None)
    course_id = attr.ib(default=None)

    version = attr.ib(default=1, metadata={'param_name': 'v'})
    hit_type = attr.ib(default='event', metadata={'param_name': 't'})

    campaign_source = attr.ib(default=DEFAULT_CAMPAIGN_SOURCE, metadata={'param_name': 'cs'})
    campaign_medium = attr.ib(default=DEFAULT_CAMPAIGN_MEDIUM, metadata={'param_name': 'cm'})
    campaign_name = attr.ib(default=None, metadata={'param_name': 'cn'})
    campaign_content = attr.ib(default=None, metadata={'param_name': 'cc'})

    event_category = attr.ib(default='email', metadata={'param_name': 'ec'})
    event_action = attr.ib(default='edx.bi.email.opened', metadata={'param_name': 'ea'})
    event_label = attr.ib(default=None, metadata={'param_name': 'el'})

    document_path = attr.ib(default=None, metadata={'param_name': 'dp'})
    document_host = attr.ib(default=None, metadata={'param_name': 'dh'})

    user_id = attr.ib(default=None, metadata={'param_name': 'uid'})
    client_id = attr.ib(default=ANONYMOUS_USER_CLIENT_ID, metadata={'param_name': 'cid'})

    def generate_image_url(self):
        """
        A URL to a clear image that can be embedded in HTML documents to track email open events.

        The query string of this URL is used to capture data about the email and visitor.
        """
        parameters = {}
        fields = attr.fields(self.__class__)
        for attribute in fields:
            value = getattr(self, attribute.name, None)
            if value is not None and 'param_name' in attribute.metadata:
                parameter_name = attribute.metadata['param_name']
                parameters[parameter_name] = str(value)

        tracking_id = self._get_tracking_id()
        if tracking_id is None:
            return None

        parameters['tid'] = tracking_id

        user_id_dimension = get_config_value_from_site_or_settings(
            "GOOGLE_ANALYTICS_USER_ID_CUSTOM_DIMENSION",
            site=self.site,
        )
        if user_id_dimension is not None and self.user_id is not None:
            parameter_name = f'cd{user_id_dimension}'
            parameters[parameter_name] = self.user_id

        if self.course_id is not None and self.event_label is None:
            param_name = fields.event_label.metadata['param_name']
            parameters[param_name] = str(self.course_id)

        return f"https://www.google-analytics.com/collect?{urlencode(parameters)}"

    def _get_tracking_id(self):
        tracking_id = get_config_value_from_site_or_settings("GOOGLE_ANALYTICS_ACCOUNT", site=self.site)
        if tracking_id is None:
            tracking_id = get_config_value_from_site_or_settings("GOOGLE_ANALYTICS_TRACKING_ID", site=self.site)
        return tracking_id
