"""
URLs for static_template_view app
"""


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
