"""
Helper Methods
"""

try:
    from braze.client import BrazeClient
except ImportError:
    BrazeClient = None
from django.conf import settings

try:
    from optimizely import optimizely
    from optimizely.config_manager import PollingConfigManager
except ImportError:
    optimizely = None
    PollingConfigManager = None


def _get_key(key_or_id, key_cls):
    """
    Helper method to get a course/usage key either from a string or a key_cls,
    where the key_cls (CourseKey or UsageKey) will simply be returned.
    """
    return (
        key_cls.from_string(key_or_id)
        if isinstance(key_or_id, str)
        else key_or_id
    )


def get_email_client():
    """ Returns a Braze client. """
    if not BrazeClient:
        return None

    braze_api_key = settings.EDX_BRAZE_API_KEY
    braze_api_url = settings.EDX_BRAZE_API_SERVER

    if not braze_api_key or not braze_api_url:
        return None

    return BrazeClient(
        api_key=braze_api_key,
        api_url=braze_api_url,
        app_id='',
    )


class OptimizelyClient:
    """ Class for instantiating an Optimizely full stack client instance. """
    optimizely_client = None

    @classmethod
    def get_optimizely_client(cls):
        if not optimizely:
            return None
        if not cls.optimizely_client:
            optimizely_sdk_key = settings.OPTIMIZELY_FULLSTACK_SDK_KEY
            if not optimizely_sdk_key:
                return None

            config_manager = PollingConfigManager(
                update_interval=10,
                sdk_key=optimizely_sdk_key,
            )
            cls.optimizely_client = optimizely.Optimizely(config_manager=config_manager)

        return cls.optimizely_client
