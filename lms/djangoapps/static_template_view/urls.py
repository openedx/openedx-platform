"""
URLs for static_template_view app
"""


from django.conf import settings
from django.urls import path, re_path

from lms.djangoapps.static_template_view import views

urlpatterns = [
    path('blog', views.render, {'template': 'blog.html'}, name="blog"),
    path('contact', views.render, {'template': 'contact.html'}, name="contact"),
    path('donate', views.render, {'template': 'donate.html'}, name="donate"),
    path('faq', views.render, {'template': 'faq.html'}, name="faq"),
    path('help', views.render, {'template': 'help.html'}, name="help_edx"),
    path('jobs', views.render, {'template': 'jobs.html'}, name="jobs"),
    path('news', views.render, {'template': 'news.html'}, name="news"),
    path('press', views.render, {'template': 'press.html'}, name="press"),
    path('media-kit', views.render, {'template': 'media-kit.html'}, name="media-kit"),
    path('copyright', views.render, {'template': 'copyright.html'}, name="copyright"),

    # Press releases
    re_path(r'^press/([_a-zA-Z0-9-]+)$', views.render_press_release, name='press_release'),
]

# Only enable URLs for those marketing links actually enabled in the
# settings. Disable URLs by marking them as None.
for key, value in settings.MKTG_URL_LINK_MAP.items():
    # Skip disabled URLs
    if value is None:
        continue

    # These urls are enabled separately
    if key == "ROOT" or key == "COURSES":  # pylint: disable=consider-using-in
        continue

    # The MKTG_URL_LINK_MAP key specifies the template filename
    template = key.lower()
    if '.' not in template:
        # Append STATIC_TEMPLATE_VIEW_DEFAULT_FILE_EXTENSION if
        # no file extension was specified in the key
        template = f"{template}.{settings.STATIC_TEMPLATE_VIEW_DEFAULT_FILE_EXTENSION}"

    # Make the assumption that the URL we want is the lowercased
    # version of the map key
    urlpatterns.append(re_path(r'^%s$' % key.lower(), views.render, {'template': template}, name=value))  # noqa: UP031
