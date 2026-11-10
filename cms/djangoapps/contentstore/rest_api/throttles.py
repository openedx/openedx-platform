"""
Throttles shared by more than one version of the contentstore REST API.
"""
from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class VideoDownloadThrottle(UserRateThrottle):
    """
    Per-user rate limit on the Studio video-download endpoint.

    The download endpoint streams a multi-video zip into the response, which
    keeps a uWSGI worker busy for the duration. Rate-limiting per user keeps
    a single course author from initiating many overlapping streams.
    """
    rate = settings.VIDEO_DOWNLOAD_RATE_LIMIT
