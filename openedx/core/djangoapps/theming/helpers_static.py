# pylint: disable=missing-module-docstring

from django.contrib.staticfiles.storage import staticfiles_storage


def get_static_file_url(asset):
    """
    Returns url of the themed asset if asset is not themed than returns the default asset url.

    Example:
        >> get_static_file_url('css/lms-main-v1.css')
        '/static/red-theme/css/lms-main-v1.css'

    Parameters:
        asset (str): asset's path relative to the static files directory

    Returns:
        (str): static asset's url
    """
    return staticfiles_storage.url(asset)
