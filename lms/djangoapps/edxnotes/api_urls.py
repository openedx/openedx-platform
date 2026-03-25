"""
API URLs for EdxNotes
"""


from django.urls import path  # noqa: I001

from lms.djangoapps.edxnotes import views

urlpatterns = [
    path('retire_user/', views.RetireUserView.as_view(), name="edxnotes_retire_user"),
]
