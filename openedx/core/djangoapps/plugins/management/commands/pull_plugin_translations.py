"""
Download the translations via atlas for the edx-platform plugins (edx_django_utils.plugins).

For the XBlock command check the `pull_xblock_translations` command.
"""

from django.conf import settings

from openedx.core.djangoapps.plugins.i18n_api import BaseAtlasPullCommand

from ...constants import plugins_locale_root
from ...i18n_api import plugin_translations_atlas_pull


class Command(BaseAtlasPullCommand):
    """
    Pull the edx_django_utils.plugins translations via atlas.

    For detailed information about atlas pull options check the atlas documentation:

     - https://github.com/openedx/openedx-atlas
    """

    def handle(self, *args, **options):
        plugin_translations_root = settings.REPO_ROOT / plugins_locale_root
        self.ensure_empty_directory(plugin_translations_root)

        atlas_pull_options = self.get_atlas_pull_options(**options)

        plugin_translations_atlas_pull(
            pull_options=atlas_pull_options,
            locale_root=plugin_translations_root,
        )
