"""
Defines the sharing sites for different social media platforms
"""
from collections import namedtuple
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from urllib.parse import urlencode

SharingSiteConfig = namedtuple(
    'SharingSiteConfig',
    [
        'name',
        'fa_icon_name',
        'url_param_name',
        'base_share_url',
        'additional_site_params'
    ],
    defaults=[{}]
)

TWITTER = SharingSiteConfig(
    name='twitter',
    fa_icon_name='fa-twitter-square',
    url_param_name='url',
    base_share_url='https://twitter.com/intent/tweet',
)

FACEBOOK = SharingSiteConfig(
    name='facebook',
    fa_icon_name='fa-facebook-square',
    url_param_name='u',
    base_share_url='https://www.facebook.com/sharer/sharer.php'
)

LINKEDIN = SharingSiteConfig(
    name='linkedin',
    fa_icon_name='fa-linkedin-square',
    url_param_name='url',
    base_share_url='https://www.linkedin.com/sharing/share-offsite/'
)

ALL_SHARING_SITES = [
    TWITTER,
    FACEBOOK,
    LINKEDIN,
]


def sharing_sites_info_for_video(video_public_url, organization=None):
    """
    Returns a list of dicts, each containing the name, fa_icon_name, and sharing_url
    """
    result = []
    for sharing_site_config in ALL_SHARING_SITES:
        sharing_site_info = {
            'name': sharing_site_config.name,
            'fa_icon_name': sharing_site_config.fa_icon_name,
            'sharing_url': sharing_url(
                video_public_url,
                sharing_site_config,
                organization=organization
            ),
        }
        result.append(sharing_site_info)
    return result


def get_share_text(social_account_handle, organization_name):
    """
    Generate the text we will pre-populate when sharing a post to social media.

    NOTE: Most of the time, we will have all info of these, but have provided
    reasonable fallbacks in case some are missing.
    """
    if social_account_handle and organization_name:
        return _(
            "Here's a fun clip from a class I'm taking on {social_account_handle} from {organization_name}.\n\n"
        ).format(
            social_account_handle=social_account_handle,
            organization_name=organization_name,
        )
    elif social_account_handle:
        return _(
            "Here's a fun clip from a class I'm taking on {social_account_handle}.\n\n"
        ).format(social_account_handle=social_account_handle)
    elif organization_name:
        return _(
            "Here's a fun clip from a class I'm taking from {organization_name}.\n\n"
        ).format(organization_name=organization_name)
    else:
        return _(
            "Here's a fun clip from a class I'm taking on {platform_name}.\n\n"
        ).format(platform_name=settings.PLATFORM_NAME)


def sharing_url(video_public_url, sharing_site_config, organization=None):
    """
    Returns the sharing url with the appropriate parameters
    """
    share_params = {
        'utm_source': sharing_site_config.name,
        'utm_medium': 'social',
        'utm_campaign': 'social-share-exp',
        sharing_site_config.url_param_name: video_public_url
    }

    # Special handling for Twitter, pre-populate share text
    if sharing_site_config.name == "twitter":
        twitter_handle = settings.PLATFORM_TWITTER_ACCOUNT
        org_name = organization['name'] if organization else None
        share_params.update({"text": get_share_text(twitter_handle, org_name)})
    else:
        share_params.update(sharing_site_config.additional_site_params)

    return sharing_site_config.base_share_url + '?' + urlencode(share_params)
