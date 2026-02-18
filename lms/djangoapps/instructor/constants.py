"""
Constants used by Instructor.
"""
from enum import StrEnum

# this is the UserPreference key for the user's recipient invoice copy
INVOICE_KEY = 'pref-invoice-copy'

# external plugins (if any) will use this constant to return context to instructor dashboard
INSTRUCTOR_DASHBOARD_PLUGIN_VIEW_NAME = 'instructor_dashboard'


class ReportType(StrEnum):
    """
    Enum for report types used in the instructor dashboard downloads API.
    These are the user-facing report type identifiers.
    """
    ENROLLED_STUDENTS = "enrolled_students"
    PENDING_ENROLLMENTS = "pending_enrollments"
    PENDING_ACTIVATIONS = "pending_activations"
    ANONYMIZED_STUDENT_IDS = "anonymized_student_ids"
    GRADE = "grade"
    PROBLEM_GRADE = "problem_grade"
    PROBLEM_RESPONSES = "problem_responses"
    ORA2_SUMMARY = "ora2_summary"
    ORA2_DATA = "ora2_data"
    ORA2_SUBMISSION_FILES = "ora2_submission_files"
    ISSUED_CERTIFICATES = "issued_certificates"
    UNKNOWN = "unknown"
