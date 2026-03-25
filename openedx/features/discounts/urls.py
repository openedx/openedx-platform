"""
Discount API URLs
"""


from django.conf import settings  # noqa: I001
from django.urls import re_path

from .views import CourseUserDiscount, CourseUserDiscountWithUserParam

urlpatterns = [
    re_path(fr'^course/{settings.COURSE_KEY_PATTERN}', CourseUserDiscount.as_view(), name='course_user_discount'),
    re_path(fr'^user/(?P<user_id>[^/]*)/course/{settings.COURSE_KEY_PATTERN}',
            CourseUserDiscountWithUserParam.as_view(),
            name='course_user_discount_with_param'),
]
