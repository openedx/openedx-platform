"""
XBlock translations pulling and compilation logic.
"""

import gettext
import os

from django.utils.encoding import force_str
from django.utils.translation import get_language, override, to_locale
from django.views.i18n import JavaScriptCatalog
from statici18n.management.commands.compilejsi18n import Command as CompileI18NJSCommand
from xblock.core import XBlock

from openedx.core.djangoapps.plugins.i18n_api import atlas_pull_by_modules
from xmodule.modulestore import api as xmodule_api


class AtlasJavaScriptCatalog(JavaScriptCatalog):
    """
    View to return the selected language catalog as a JavaScript library.

    This extends the JavaScriptCatalog class to allow custom domain and locale_dir.
    """

    translation = None

    def get(self, request, *args, **kwargs):
        """
        Return the selected language catalog as a JavaScript library.

        This overrides the JavaScriptCatalog.get() method class to allow custom locale_dir.
        """
        selected_language = get_language()
        locale = to_locale(selected_language)
        domain = kwargs['domain']
        locale_dir = kwargs['locale_dir']
        # Using GNUTranslations instead of DjangoTranslation to allow custom locale_dir without needing
        # to use a custom `text.mo` translation domain.
        self.translation = gettext.translation(domain, localedir=locale_dir, languages=[locale])
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    @classmethod
    def simulate_get_request(cls, locale, domain, locale_dir):
        """
        Simulate a GET request to the JavaScriptCatalog view.

        Return:
            str: The rendered JavaScript catalog.
        """
        with override(locale):
            catalog_view = cls()
            response = catalog_view.get(
                request=None,  # we are passing None as the request, as the request
                               # object is currently not used by django
                domain=domain,
                locale_dir=locale_dir,
            )
            return force_str(response.content)


def mo_file_to_js_namespaced_catalog(xblock_conf_locale_dir, locale, domain, namespace):
    """
    Compile .mo to .js gettext catalog and wrap it in a namespace via the `compilejsi18n` command helpers.
    """
    rendered_js = AtlasJavaScriptCatalog.simulate_get_request(
        locale=locale,
        locale_dir=xblock_conf_locale_dir,
        domain=domain,
    )

    # The `django-statici18n` package has a non-standard code license, therefore we're using its private API
    # to avoid copying the code into this repository and running into licensing issues.
    compile_i18n_js_command = CompileI18NJSCommand()
    namespaced_catalog_js_code = compile_i18n_js_command._get_namespaced_catalog(  # pylint: disable=protected-access
        rendered_js=rendered_js,
        namespace=namespace,
    )

    return namespaced_catalog_js_code


def xblocks_atlas_pull(pull_options):
    """
    Atlas pull the translations for the XBlocks that are installed.
    """
    xblock_module_names = get_non_xmodule_xblock_module_names()

    atlas_pull_by_modules(
        module_names=xblock_module_names,
        locale_root=xmodule_api.get_python_locale_root(),
        pull_options=pull_options,
    )


def compile_xblock_js_messages():
    """
    Compile the XBlock JavaScript messages from .mo file into .js files.
    """
    for xblock_module, xblock_class in get_non_xmodule_xblocks():
        xblock_conf_locale_dir = xmodule_api.get_python_locale_root() / xblock_module
        i18n_js_namespace = xblock_class.get_i18n_js_namespace()

        for locale_dir in xblock_conf_locale_dir.iterdir():
            locale_code = str(locale_dir.basename())
            locale_messages_dir = locale_dir / 'LC_MESSAGES'
            js_translations_domain = None
            for domain in ['djangojs', 'django']:
                po_file_path = locale_messages_dir / f'{domain}.mo'
                if po_file_path.exists():
                    if not js_translations_domain:
                        # Select which file to compile to `django.js`, while preferring `djangojs` over `django`
                        js_translations_domain = domain

            if js_translations_domain and i18n_js_namespace:
                js_i18n_file_path = xmodule_api.get_javascript_i18n_file_path(xblock_module, locale_code)
                os.makedirs(js_i18n_file_path.dirname(), exist_ok=True)
                js_namespaced_catalog = mo_file_to_js_namespaced_catalog(
                    xblock_conf_locale_dir=xblock_conf_locale_dir,
                    locale=locale_code,
                    domain=js_translations_domain,
                    namespace=i18n_js_namespace,
                )

                with open(js_i18n_file_path, 'w', encoding='utf-8') as f:
                    f.write(js_namespaced_catalog)


def get_non_xmodule_xblocks():
    """
    Returns a list of XBlock classes with their module name excluding edx-platform/xmodule xblocks.
    """
    xblock_classes = []
    for _xblock_tag, xblock_class in XBlock.load_classes():
        xblock_module_name = xmodule_api.get_root_module_name(xblock_class)
        if xblock_module_name != 'xmodule':
            # XBlocks in edx-platform/xmodule are already translated in edx-platform/conf/locale
            # So there is no need to add special handling for them.
            xblock_classes.append(
                (xblock_module_name, xblock_class),
            )

    return xblock_classes


def get_non_xmodule_xblock_module_names():
    """
    Returns a list of module names for the plugins that supports translations excluding `xmodule`.
    """
    xblock_module_names = set(
        xblock_module_name
        for xblock_module_name, _xblock_class in get_non_xmodule_xblocks()
    )

    sorted_xblock_module_names = list(sorted(xblock_module_names))
    return sorted_xblock_module_names
