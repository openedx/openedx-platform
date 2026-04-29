"""Production settings for Credentials"""


def plugin_settings(settings):  # pylint: disable=missing-function-docstring
    # Credentials Settings
    settings.CREDENTIALS_INTERNAL_SERVICE_URL = settings.ENV_TOKENS.get(
        'CREDENTIALS_INTERNAL_SERVICE_URL', settings.CREDENTIALS_INTERNAL_SERVICE_URL
    )
    settings.CREDENTIALS_PUBLIC_SERVICE_URL = settings.ENV_TOKENS.get(
        'CREDENTIALS_PUBLIC_SERVICE_URL', settings.CREDENTIALS_PUBLIC_SERVICE_URL
    )
