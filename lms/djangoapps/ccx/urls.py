"""
URLs for the CCX Feature.
"""


from django.urls import path, re_path  # noqa: I001

from . import views

urlpatterns = [
    path('ccx_coach', views.dashboard, name='ccx_coach_dashboard'),
    path('create_ccx', views.create_ccx, name='create_ccx'),
    path('save_ccx', views.save_ccx, name='save_ccx'),
    path('ccx_schedule', views.ccx_schedule, name='ccx_schedule'),
    path('ccx-manage-students', views.ccx_students_management, name='ccx-manage-students'),

    # Grade book
    path('ccx_gradebook', views.ccx_gradebook, name='ccx_gradebook'),
    path('ccx_gradebook/<int:offset>', views.ccx_gradebook, name='ccx_gradebook'),

    re_path(r'^ccx_grades.csv$', views.ccx_grades_csv, name='ccx_grades_csv'),
    path('ccx_set_grading_policy', views.set_grading_policy, name='ccx_set_grading_policy'),
]
