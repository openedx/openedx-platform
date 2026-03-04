"""
URL definitions for the verify_student app.
"""


from django.conf import settings
from django.urls import path, re_path

from lms.djangoapps.verify_student import views

IDV_RECEIPT_ID_PATTERN = r'(?P<receipt_id>[0-9a-z]{8}-([0-9a-z]{4}-){3}[0-9a-z]{12})'

urlpatterns = [
    # The user is starting the verification / payment process,
    # most likely after enrolling in a course and selecting
    # a "verified" track.
    re_path(
        fr'^start-flow/{settings.COURSE_ID_PATTERN}/$',
        # Pylint seems to dislike the as_view() method because as_view() is
        # decorated with `classonlymethod` instead of `classmethod`.
        views.PayAndVerifyView.as_view(),
        name="verify_student_start_flow",
        kwargs={
            'message': views.PayAndVerifyView.FIRST_TIME_VERIFY_MSG
        }
    ),

    # This is for A/B testing.
    re_path(
        fr'^begin-flow/{settings.COURSE_ID_PATTERN}/$',
        views.PayAndVerifyView.as_view(),
        name="verify_student_begin_flow",
        kwargs={
            'message': views.PayAndVerifyView.FIRST_TIME_VERIFY_MSG
        }
    ),

    # The user is enrolled in a non-paid mode and wants to upgrade.
    # This is the same as the "start verification" flow,
    # except with slight messaging changes.
    re_path(
        fr'^upgrade/{settings.COURSE_ID_PATTERN}/$',
        views.PayAndVerifyView.as_view(),
        name="verify_student_upgrade_and_verify",
        kwargs={
            'message': views.PayAndVerifyView.UPGRADE_MSG
        }
    ),

    # The user has paid and still needs to verify.
    # Since the user has "just paid", we display *all* steps
    # including payment.  The user resumes the flow
    # from the verification step.
    # Note that if the user has already verified, this will redirect
    # to the dashboard.
    re_path(
        fr'^verify-now/{settings.COURSE_ID_PATTERN}/$',
        views.PayAndVerifyView.as_view(),
        name="verify_student_verify_now",
        kwargs={
            'always_show_payment': True,
            'current_step': views.PayAndVerifyView.FACE_PHOTO_STEP,
            'message': views.PayAndVerifyView.VERIFY_NOW_MSG
        }
    ),

    path(
        'create_order',
        views.create_order,
        name="verify_student_create_order"
    ),

    path(
        'results_callback',
        views.results_callback,
        name="verify_student_results_callback",
    ),

    path(
        'submit-photos/',
        views.SubmitPhotosView.as_view(),
        name="verify_student_submit_photos"
    ),

    path(
        'status/',
        views.VerificationStatusAPIView.as_view(),
        name="verification_status_api"
    ),


    # End-point for reverification
    # Reverification occurs when a user's initial verification attempt
    # is denied or expires.  The user is allowed to retry by submitting
    # new photos.  This is different than *in-course* reverification,
    # in which a student submits only face photos, which are matched
    # against the ID photo from the user's initial verification attempt.
    path(
        'reverify',
        views.ReverifyView.as_view(),
        name="verify_student_reverify"
    ),

    re_path(
        fr'^photo-urls/{IDV_RECEIPT_ID_PATTERN}$',
        views.PhotoUrlsView.as_view(),
        name="verification_photo_urls"
    ),

    re_path(
        fr'^decrypt-idv-images/face/{IDV_RECEIPT_ID_PATTERN}$',
        views.DecryptFaceImageView.as_view(),
        name="verification_decrypt_face_image"
    ),

    re_path(
        fr'^decrypt-idv-images/photo-id/{IDV_RECEIPT_ID_PATTERN}$',
        views.DecryptPhotoIDImageView.as_view(),
        name="verification_decrypt_photo_id_image"
    ),
]

# Fake response page for incourse reverification ( software secure )
if settings.ENABLE_SOFTWARE_SECURE_FAKE:
    from lms.djangoapps.verify_student.tests.fake_software_secure import SoftwareSecureFakeView
    urlpatterns += [
        path('software-secure-fake-response', SoftwareSecureFakeView.as_view()),
    ]
