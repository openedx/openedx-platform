"""
Utils for video_pipeline app.
"""
from django.conf import settings
from edx_rest_api_client.client import OAuthAPIClient


def create_video_pipeline_api_client(api_client_id, api_client_secret):
    """
    Returns an API client which can be used to make Video Pipeline API requests.

    Arguments:
        api_client_id(unicode): Video pipeline client id.
        api_client_secret(unicode): Video pipeline client secret.
    """
    return OAuthAPIClient(settings.LMS_ROOT_URL, api_client_id, api_client_secret)
