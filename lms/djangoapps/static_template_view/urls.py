"""
URLs for static_template_view app
"""


from django.urls import path, re_path

from lms.djangoapps.static_template_view import views

urlpatterns = [
    path('copyright', views.render, {'template': 'copyright.html'}, name="copyright"),

    # Press releases
    re_path(r'^press/([_a-zA-Z0-9-]+)$', views.render_press_release, name='press_release'),
]
