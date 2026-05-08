# pylint: disable=missing-module-docstring

from django.conf import settings as django_settings
from django.contrib.staticfiles.storage import staticfiles_storage
from pipeline.conf import settings
from pipeline.packager import Packager
from pipeline.utils import guess_type

from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.static_replace import try_staticfiles_lookup


def compressed_css(package_name, raw=False):  # pylint: disable=missing-function-docstring
    package = settings.STYLESHEETS.get(package_name, {})
    if package:
        package = {package_name: package}
    packager = Packager(css_packages=package, js_packages={})

    package = packager.package_for('css', package_name)

    if settings.PIPELINE_ENABLED:
        return render_css(package, package.output_filename, raw=raw)
    else:
        paths = packager.compile(package.paths)
        return render_individual_css(package, paths, raw=raw)


def render_css(package, path, raw=False):  # pylint: disable=missing-function-docstring
    template_name = package.template_name or "mako/css.html"
    context = package.extra_context

    url = try_staticfiles_lookup(path)
    if raw:
        url += "?raw"
    context.update({
        'type': guess_type(path, 'text/css'),
        'url': url,
    })
    return render_to_string(template_name, context)


def render_individual_css(package, paths, raw=False):
    tags = [render_css(package, path, raw) for path in paths]
    return '\n'.join(tags)


def compressed_js(package_name):  # pylint: disable=missing-function-docstring
    package = settings.JAVASCRIPT.get(package_name, {})
    if package:
        package = {package_name: package}
    packager = Packager(css_packages={}, js_packages=package)

    package = packager.package_for('js', package_name)

    if settings.PIPELINE_ENABLED:
        return render_js(package, package.output_filename)
    else:
        paths = packager.compile(package.paths)
        templates = packager.pack_templates(package)
        return render_individual_js(package, paths, templates)


def render_js(package, path):  # pylint: disable=missing-function-docstring
    template_name = package.template_name or "mako/js.html"
    context = package.extra_context
    context.update({
        'type': guess_type(path, 'text/javascript'),
        'url': try_staticfiles_lookup(path)
    })
    return render_to_string(template_name, context)


def render_inline_js(package, js):  # pylint: disable=missing-function-docstring
    context = package.extra_context
    context.update({
        'source': js
    })
    return render_to_string("mako/inline_js.html", context)


def render_individual_js(package, paths, templates=None):
    tags = [render_js(package, js) for js in paths]
    if templates:
        tags.append(render_inline_js(package, templates))
    return '\n'.join(tags)


def render_require_js_path_overrides(path_overrides):
    """Render JavaScript to override default RequireJS paths.

    The Django pipeline appends a hash to JavaScript files,
    so if the JS asset isn't included in the bundle for the page,
    we need to tell RequireJS where to look.

    For example:

        "js/vendor/jquery.js" --> "js/vendor/jquery.abcd1234"

    To achive this we will add overrided paths in requirejs config at runtime.

    So that any reference to 'jquery' in a JavaScript module
    will cause RequireJS to load '/static/js/vendor/jquery.abcd1234.js'

    If running in DEBUG mode (as in devstack), the resolved JavaScript URLs
    won't contain hashes, so the new paths will match the original paths.

    Arguments:
        path_overrides (dict): Mapping of RequireJS module names to
            filesystem paths.

    Returns:
        unicode: The HTML of the <script> tag with the path overrides.

    """
    # Render the <script> tag that overrides the paths
    # Note: We don't use a Mako template to render this because Mako apparently
    # acquires a lock when loading templates, which can lead to a deadlock if
    # this function is called from within another template.
    # The rendered <script> tag with overrides should be included *after*
    # the application's RequireJS config, which defines a `require` object.
    html = '''<script type="text/javascript">
        (function (require) {{
          require.config({{
              paths: {{
                {overrides}
            }}
          }});
        }}).call(this, require || RequireJS.require);
    </script>'''

    new_paths = []
    for module in path_overrides:
        # Calculate the full URL, including any hashes added to the filename by the pipeline.
        # This will also include the base static URL (for example, "/static/") and the
        # ".js" extension.
        actual_url = staticfiles_storage.url(path_overrides[module])

        # RequireJS assumes that every file it tries to load has a ".js" extension, so
        # we need to remove ".js" from the module path.
        # RequireJS also already has a base URL set to the base static URL, so we can remove that.
        path = actual_url.replace('.js', '').replace(django_settings.STATIC_URL, '')

        new_paths.append(f"'{module}': '{path}'")

    return html.format(overrides=',\n'.join(new_paths))
