
"""
Instructor API endpoint urls.
"""

from django.urls import path, re_path

from lms.djangoapps.instructor.views import api, gradebook_api, api_v2
from openedx.core.constants import COURSE_ID_PATTERN

# These endpoints are exposing existing views in a way that can be used by MFEs
# or other API clients. They are currently versioned at `v1` since they have
# been around without major changes for a while and will probably not be changed
# in incompatible ways. If they do need incompatible changes for use via MFEs
# then new v2 endpoints can be introduced.
v1_api_urls = [
    re_path(rf'^tasks/{COURSE_ID_PATTERN}$', api.InstructorTasks.as_view(), name='list_instructor_tasks', ),
    re_path(rf'^reports/{COURSE_ID_PATTERN}$', api.ReportDownloads.as_view(), name='list_report_downloads', ),
    re_path(rf'^reports/{COURSE_ID_PATTERN}/generate/problem_responses$', api.ProblemResponseReportInitiate.as_view(),
            name='generate_problem_responses', ),
]

v2_api_urls = [
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}$',
        api_v2.CourseMetadataView.as_view(),
        name='course_metadata'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/instructor_tasks$',
        api_v2.InstructorTaskListView.as_view(),
        name='instructor_tasks'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/change_due_date$',
        api_v2.ChangeDueDateView.as_view(),
        name='change_due_date'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/graded_subsections$',
        api_v2.GradedSubsectionsView.as_view(),
        name='graded_subsections'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/unit_extensions$',
        api_v2.UnitExtensionsView.as_view(),
        name='unit_extensions'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/ora$',
        api_v2.ORAView.as_view(),
        name='ora_assessments'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/reports$',
        api_v2.ReportDownloadsView.as_view(),
        name='report_downloads'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/reports/(?P<report_type>[^/]+)/generate$',
        api_v2.GenerateReportView.as_view(),
        name='generate_report'
    ),
    re_path(
        rf'^courses/{COURSE_ID_PATTERN}/ora_summary$',
        api_v2.ORASummaryView.as_view(),
        name='ora_summary'
    ),
]

urlpatterns = [
    path('students_update_enrollment', api.StudentsUpdateEnrollmentView.as_view(), name='students_update_enrollment'),
    path('register_and_enroll_students', api.RegisterAndEnrollStudents.as_view(), name='register_and_enroll_students'),
    path('list_course_role_members', api.ListCourseRoleMembersView.as_view(), name='list_course_role_members'),
    path('modify_access', api.ModifyAccess.as_view(), name='modify_access'),
    path('bulk_beta_modify_access', api.BulkBetaModifyAccess.as_view(), name='bulk_beta_modify_access'),
    path('get_problem_responses', api.GetProblemResponses.as_view(), name='get_problem_responses'),
    path('get_issued_certificates/', api.GetIssuedCertificates.as_view(), name='get_issued_certificates'),
    re_path(r'^get_students_features(?P<csv>/csv)?$', api.GetStudentsFeatures.as_view(), name='get_students_features'),
    path('get_grading_config', api.GetGradingConfig.as_view(), name='get_grading_config'),
    path('get_students_who_may_enroll', api.GetStudentsWhoMayEnroll.as_view(), name='get_students_who_may_enroll'),
    path('get_enrolled_students_with_inactive_account', api.GetInactiveEnrolledStudents.as_view(),
         name='get_enrolled_students_with_inactive_account'),
    path('get_anon_ids', api.GetAnonIds.as_view(), name='get_anon_ids'),
    path('get_student_enrollment_status', api.GetStudentEnrollmentStatus.as_view(),
         name="get_student_enrollment_status"),
    path('get_student_progress_url', api.StudentProgressUrl.as_view(), name='get_student_progress_url'),
    path('reset_student_attempts', api.ResetStudentAttempts.as_view(), name='reset_student_attempts'),
    path('rescore_problem', api.RescoreProblem.as_view(), name='rescore_problem'),
    path('override_problem_score', api.OverrideProblemScoreView.as_view(), name='override_problem_score'),
    path('reset_student_attempts_for_entrance_exam', api.ResetStudentAttemptsForEntranceExam.as_view(),
         name='reset_student_attempts_for_entrance_exam'),
    path('rescore_entrance_exam', api.RescoreEntranceExamView.as_view(), name='rescore_entrance_exam'),
    path('list_entrance_exam_instructor_tasks', api.ListEntranceExamInstructorTasks.as_view(),
         name='list_entrance_exam_instructor_tasks'),
    path('mark_student_can_skip_entrance_exam', api.MarkStudentCanSkipEntranceExam.as_view(),
         name='mark_student_can_skip_entrance_exam'),
    path('list_instructor_tasks', api.ListInstructorTasks.as_view(), name='list_instructor_tasks'),
    path('list_background_email_tasks', api.ListBackgroundEmailTasks.as_view(), name='list_background_email_tasks'),
    path('list_email_content', api.ListEmailContent.as_view(), name='list_email_content'),
    path('list_forum_members', api.ListForumMembers.as_view(), name='list_forum_members'),
    path('update_forum_role_membership', api.UpdateForumRoleMembership.as_view(), name='update_forum_role_membership'),
    path('change_due_date', api.ChangeDueDate.as_view(), name='change_due_date'),
    path('send_email', api.SendEmail.as_view(), name='send_email'),
    path('reset_due_date', api.ResetDueDate.as_view(), name='reset_due_date'),
    path('show_unit_extensions', api.ShowUnitExtensionsView.as_view(), name='show_unit_extensions'),
    path('show_student_extensions', api.ShowStudentExtensions.as_view(), name='show_student_extensions'),

    # proctored exam downloads...
    path('get_proctored_exam_results', api.GetProctoredExamResults.as_view(), name='get_proctored_exam_results'),

    # Grade downloads...
    path('list_report_downloads', api.ListReportDownloads.as_view(), name='list_report_downloads'),
    path('calculate_grades_csv', api.CalculateGradesCsvView.as_view(), name='calculate_grades_csv'),
    path('problem_grade_report', api.ProblemGradeReport.as_view(), name='problem_grade_report'),

    # Reports..
    path('get_course_survey_results', api.GetCourseSurveyResults.as_view(), name='get_course_survey_results'),
    path('export_ora2_data', api.ExportOra2DataView.as_view(), name='export_ora2_data'),
    path('export_ora2_summary', api.ExportOra2SummaryView.as_view(), name='export_ora2_summary'),

    path('export_ora2_submission_files', api.ExportOra2SubmissionFilesView.as_view(),
         name='export_ora2_submission_files'),

    # spoc gradebook
    path('gradebook', gradebook_api.spoc_gradebook, name='spoc_gradebook'),

    path('gradebook/<int:offset>', gradebook_api.spoc_gradebook, name='spoc_gradebook'),

    # Cohort management
    path('add_users_to_cohorts', api.AddUsersToCohorts.as_view(), name='add_users_to_cohorts'),

    # Unified endpoint for Certificate tasks
    path('certificate_task/<action>', api.CertificateTask.as_view(), name='certificate_task'),

    # Certificates
    path('enable_certificate_generation', api.enable_certificate_generation, name='enable_certificate_generation'),
    path('start_certificate_generation', api.StartCertificateGeneration.as_view(), name='start_certificate_generation'),
    path('start_certificate_regeneration', api.StartCertificateRegeneration.as_view(),
         name='start_certificate_regeneration'),
    path('certificate_exception_view/', api.CertificateExceptionView.as_view(), name='certificate_exception_view'),
    re_path(r'^generate_certificate_exceptions/(?P<generate_for>[^/]*)', api.GenerateCertificateExceptions.as_view(),
            name='generate_certificate_exceptions'),
    path('generate_bulk_certificate_exceptions', api.GenerateBulkCertificateExceptions.as_view(),
         name='generate_bulk_certificate_exceptions'),
    path(
        'certificate_invalidation_view/',
        api.CertificateInvalidationView.as_view(),
        name='certificate_invalidation_view'
    ),
]
