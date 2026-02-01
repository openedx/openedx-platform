# pylint: disable=missing-docstring
from urllib.parse import urlparse

from crum import get_current_request
from django import template
from django.utils.safestring import mark_safe

from openedx.core.djangoapps.ace_common.tracking import CampaignTrackingInfo, GoogleAnalyticsTrackingPixel
from openedx.core.djangolib.markup import HTML

register = template.Library()  # pylint: disable=invalid-name


@register.simple_tag(takes_context=True)
def with_link_tracking(context, url):
    """
    Modifies the provided URL to ensure it is safe for usage in an email template and adds UTM parameters to it.

    The provided URL can be relative or absolute. If it is relative, it will be made absolute.

    All URLs will be augmented to include UTM parameters so that clicks can be tracked.

    Args:
        context (dict): The template context. Must include a "message". A request must be provided in this template
            context or be retrievable using crum.
        url (str): The url to rewrite.

    Returns:
        str: The URL as an absolute URL with appropriate query string parameters that allow clicks to be tracked.

    """
    site, _user, message = _get_variables_from_context(context, 'with_link_tracking')

    campaign = CampaignTrackingInfo(
        source=message.app_label,
        campaign=message.name,
        content=message.uuid,
    )
    course_ids = context.get('course_ids')
    if course_ids is not None and len(course_ids) > 0:
        campaign.term = course_ids[0]

    return mark_safe(
        modify_url_to_track_clicks(
            ensure_url_is_absolute(site, url),
            campaign=campaign
        )
    )


def _get_variables_from_context(context, tag_name):
    if 'request' in context:
        request = context['request']
    else:
        request = get_current_request()

    if request is None:
        raise template.VariableDoesNotExist(
            'The {} template tag requires a "request" to be present in the template context. Consider using '
            '"emulate_http_request" if you are rendering the template in a celery task.'.format(tag_name)
        )

    message = context.get('message')
    if message is None:
        raise template.VariableDoesNotExist(
            f'The {tag_name} template tag requires a "message" to be present in the template context.'
        )

    return request.site, request.user, message


@register.simple_tag(takes_context=True)
def google_analytics_tracking_pixel(context):
    """
    If configured, inject a google analytics tracking pixel into the template.

    This tracking pixel will allow email open events to be tracked.

    Args:
        context (dict): The template context. Must include a "message". A request must be provided in this template
            context or be retrievable using crum.

    Returns:
        str: A string containing an HTML image tag that implements the GA measurement protocol or an empty string if
             GA is not configured. For this to work, the site or settings must include the GA tracking ID.
    """
    image_url = _get_google_analytics_tracking_url(context)
    if image_url is not None:
        return mark_safe(
            HTML('<img src="{0}" alt="" role="presentation" aria-hidden="true" />').format(HTML(image_url))
        )
    else:
        return ''


def _get_google_analytics_tracking_url(context):
    site, user, message = _get_variables_from_context(context, 'google_analytics_tracking_pixel')

    user_id = user.id if user else None

    pixel = GoogleAnalyticsTrackingPixel(
        site=site,
        user_id=user_id,
        campaign_source=message.app_label,
        campaign_name=message.name,
        campaign_content=message.uuid,
        document_path='/email/{}/{}/{}/{}'.format(
            message.app_label,
            message.name,
            message.send_uuid,
            message.uuid,
        ),
        document_host=site.domain.rstrip('/')
    )
    course_ids = context.get('course_ids')
    if course_ids is not None and len(course_ids) > 0:
        pixel.course_id = course_ids[0]

    return pixel.generate_image_url()


def modify_url_to_track_clicks(url, campaign=None):
    """
    Given a URL, this method modifies the query string parameters to include UTM tracking parameters.

    These UTM codes are used to by Google Analytics to identify the source of traffic. This will help us better
    understand how users behave when they come to the site by clicking a link in this email.

    Arguments:
        url (str): pass
        campaign (CampaignTrackingInfo): pass

    Returns:
        str: The url with appropriate query string parameters.
    """
    parsed_url = urlparse(url)
    if campaign is None:
        campaign = CampaignTrackingInfo()
    modified_url = parsed_url._replace(query=campaign.to_query_string(parsed_url.query))
    return modified_url.geturl()


def ensure_url_is_absolute(site, relative_path):
    """
    Add site.domain to the beginning of the given relative path.

    If the given URL is already absolute (has a netloc part), then it is just returned.
    """
    if bool(urlparse(relative_path).netloc):
        # Given URL is already absolute
        url = relative_path
    else:
        root = site.domain.rstrip('/')
        relative_path = relative_path.lstrip('/')
        url = f'https://{root}/{relative_path}'
    return url
