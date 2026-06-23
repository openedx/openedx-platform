"""Third party authentication. """


from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers


def is_enabled():
    """Check whether third party authentication has been enabled. """

    # We do this import internally to avoid initializing settings prematurely
    from django.conf import settings

    return configuration_helpers.get_value(
        "ENABLE_THIRD_PARTY_AUTH",
        settings.ENABLE_THIRD_PARTY_AUTH
    )
