# pylint: disable=missing-module-docstring

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template import Engine, TemplateDoesNotExist, engines
from django.template.loaders.app_directories import Loader as AppDirectoriesLoader
from django.template.loaders.filesystem import Loader as FilesystemLoader

from common.djangoapps.edxmako.template import Template
from openedx.core.lib.tempdir import mkdtemp_clean

log = logging.getLogger(__name__)


class MakoLoader:
    """
    This is a Django loader object which will load the template as a
    Mako template if the first line is "## mako". It is based off Loader
    in django.template.loaders.base.
    We need this in order to be able to include mako templates inside main_django.html.
    """

    is_usable = False
    supports_recursion = True

    def __init__(self, base_loader):
        # base_loader is an instance of a BaseLoader subclass
        self.base_loader = base_loader

        module_directory = getattr(settings, 'MAKO_MODULE_DIR', None)

        if module_directory is None:
            log.warning("For more caching of mako templates, set the MAKO_MODULE_DIR in settings!")
            module_directory = mkdtemp_clean()

        self.module_directory = module_directory

    def __call__(self, template_name, template_dirs=None):
        return self.load_template(template_name)

    # pylint: disable=unused-argument
    def get_template(self, template_name, template_dirs=None, skip=None):
        return self.load_template(template_name)

    def load_template(self, template_name):
        """
        Method returns loads and returns template if it exists
        """
        source, origin = self.load_template_source(template_name)

        # In order to allow dynamic template overrides, we need to cache templates based on their absolute paths
        # rather than relative paths, overriding templates would have same relative paths.
        module_directory = self.module_directory.rstrip("/") + f"/{hash(origin.name)}/"

        if source.startswith("## mako\n"):
            # This is a mako template
            template = Template(filename=origin.name,
                                module_directory=module_directory,
                                input_encoding='utf-8',
                                output_encoding='utf-8',
                                default_filters=['decode.utf8'],
                                encoding_errors='replace',
                                uri=template_name,
                                engine=engines['mako'])
            return template
        else:
            # This is a regular template
            try:
                template = Engine.get_default().from_string(source)
                return template
            except ImproperlyConfigured:  # pylint: disable=try-except-raise
                # Either no DjangoTemplates engine was configured -or- multiple engines
                # were configured, making the get_default() call above fail.
                raise
            except TemplateDoesNotExist:
                # If compiling the loaded template raises TemplateDoesNotExist, back off to
                # returning the source and display name for the requested template.
                # This allows for eventual correct identification of the actual template that does
                # not exist.
                return source, origin.name

    def load_template_source(self, template_name):
        """
        Method returns the contents of the  template
        """
        for origin in self.base_loader.get_template_sources(template_name):
            try:
                return self.base_loader.get_contents(origin), origin
            except TemplateDoesNotExist:
                pass
        raise TemplateDoesNotExist(template_name)

    def reset(self):
        self.base_loader.reset()


class MakoFilesystemLoader(MakoLoader):  # pylint: disable=missing-class-docstring
    is_usable = True
    _accepts_engine_in_init = True

    def __init__(self, *args):
        MakoLoader.__init__(self, FilesystemLoader(*args))  # pylint: disable=no-value-for-parameter


class MakoAppDirectoriesLoader(MakoLoader):  # pylint: disable=missing-class-docstring
    is_usable = True
    _accepts_engine_in_init = True

    def __init__(self, *args):
        MakoLoader.__init__(self, AppDirectoriesLoader(*args))  # pylint: disable=no-value-for-parameter
