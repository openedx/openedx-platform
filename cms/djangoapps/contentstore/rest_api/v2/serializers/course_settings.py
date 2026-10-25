"""Serializers for a course's Studio settings."""

from rest_framework import serializers

MINIMAL_VIEW = "minimal"
FULL_VIEW = "full"


class PrerequisiteCourseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A course the caller may set as a prerequisite of this course."""

    course_key = serializers.CharField(help_text="Key of the course.")
    display_name = serializers.CharField(
        allow_null=True,
        help_text="Name of the course, or null when it has none.",
    )
    lms_link = serializers.CharField(
        allow_null=True,
        help_text=(
            "Address of the course in the LMS, or null when no LMS address is configured or the course "
            "key is in the deprecated slash-separated form."
        ),
    )
    number = serializers.CharField(help_text="Course number as shown to learners.")
    org = serializers.CharField(help_text="Organization as shown to learners.")
    rerun_link = serializers.CharField(
        allow_null=True,
        help_text="Studio path of the page that starts a rerun of the course, or null for a slash-separated key.",
    )
    run = serializers.CharField(help_text="Run of the course.")
    url = serializers.CharField(
        allow_null=True,
        help_text="Studio path of the course, or null for a slash-separated key.",
    )

    class Meta:
        ref_name = "PrerequisiteCourse.v2"


class CreditRequirementSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One requirement a learner meets to become eligible for credit in the course."""

    name = serializers.CharField(help_text="Name of the requirement, unique within its namespace.")
    display_name = serializers.CharField(help_text="Name of the requirement as shown to authors.")
    criteria = serializers.DictField(
        help_text="What meeting the requirement takes, such as {\"min_grade\": 0.8} for a grade requirement.",
    )

    class Meta:
        ref_name = "CreditRequirement.v2"


class CourseSettingsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    What Studio's course settings pages need to know about a course and the site.

    ``credit_requirements``, ``language_options`` and
    ``possible_pre_requisite_courses`` are returned only with ``view=full``.
    """

    about_page_editable = serializers.BooleanField(
        help_text="Whether the course about page is edited in Studio, which it is not when a publishing service "
                  "manages the course's organization.",
    )
    can_show_certificate_available_date_field = serializers.BooleanField(
        help_text="Whether authors may set the date certificates become available: automatic certificate "
                  "generation is on and the course is instructor-paced.",
    )
    content_experiments_enabled = serializers.BooleanField(
        help_text="Whether content experiments are turned on in the course's advanced settings and installed on "
                  "the site, so experiment group configurations apply to it. Read this instead of the "
                  "should_show_experiment_groups flag of the course's group configurations listing, which "
                  "its successor does not return.",
    )
    course_display_name = serializers.CharField(
        allow_null=True,
        help_text="Name of the course, or null when it has none.",
    )
    course_display_name_with_default = serializers.CharField(
        help_text="Name of the course, or a name made from its key when it has none.",
    )
    credit_eligibility_enabled = serializers.BooleanField(
        help_text="Whether credit eligibility is turned on for the site.",
    )
    credit_requirements = serializers.DictField(
        child=serializers.ListField(child=CreditRequirementSerializer()),
        required=False,
        help_text="The course's credit requirements grouped by namespace, such as grade or proctored_exam. "
                  "Returned only with view=full, and then only when credit eligibility is on and the course is "
                  "a credit course.",
    )
    enable_extended_course_details = serializers.BooleanField(
        help_text="Whether the extended course details, such as instructors and learning outcomes, are edited "
                  "for courses of this organization.",
    )
    enrollment_end_editable = serializers.BooleanField(
        help_text="Whether the caller may change the enrollment end date: global staff always may, others only "
                  "when no publishing service manages the course's organization.",
    )
    is_credit_course = serializers.BooleanField(
        help_text="Whether the course offers credit. Always false while credit eligibility is off.",
    )
    is_entrance_exams_enabled = serializers.BooleanField(
        help_text="Whether entrance exams are turned on for the site.",
    )
    is_prerequisite_courses_enabled = serializers.BooleanField(
        help_text="Whether prerequisite courses are turned on for the site.",
    )
    language_options = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(), min_length=2, max_length=2),
        required=False,
        help_text="Languages a course may be taught in, as [code, name] pairs. Returned only with view=full.",
    )
    licensing_enabled = serializers.BooleanField(
        help_text="Whether authors may choose the license of course content.",
    )
    lms_link_for_about_page = serializers.CharField(
        help_text="Address learners use to reach the course about page.",
    )
    marketing_enabled = serializers.BooleanField(
        help_text="Whether course marketing settings are shown. Always true.",
    )
    mfe_proctored_exam_settings_url = serializers.CharField(
        allow_blank=True,
        help_text="Path, within the authoring app, of the course's proctored exam settings page, or an empty "
                  "string when that page is not served by the authoring app. Read this instead of the "
                  "mfe_proctored_exam_settings_url link of the course's group configurations listing, which "
                  "its successor does not return.",
    )
    platform_name = serializers.CharField(help_text="Name of the site.")
    possible_pre_requisite_courses = PrerequisiteCourseSerializer(
        many=True,
        required=False,
        help_text="Courses the caller may set as prerequisites: every course the caller may view in Studio "
                  "except this one and courses still being created as reruns, oldest first. Returned only "
                  "with view=full, and then only when prerequisite courses are on.",
    )
    short_description_editable = serializers.BooleanField(
        help_text="Whether the course's short description is edited in Studio for courses of this organization.",
    )
    show_min_grade_warning = serializers.BooleanField(
        help_text="Whether to warn that the course is a credit course with no minimum grade for credit set.",
    )
    sidebar_html_enabled = serializers.BooleanField(
        help_text="Whether authors may add HTML to the sidebar of the course about page.",
    )
    upgrade_deadline = serializers.DateTimeField(
        allow_null=True,
        help_text="When the course's verified mode stops being sold, or null when it has no such mode or "
                  "no deadline.",
    )

    class Meta:
        ref_name = "CourseSettings.v2"


class CourseSettingsQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a read of a course's settings."""

    view = serializers.ChoiceField(
        choices=[MINIMAL_VIEW, FULL_VIEW],
        default=MINIMAL_VIEW,
        help_text=(
            "How much to return. 'minimal' returns the course's settings and the site's switches; "
            "'full' also returns the language list, the courses that may be prerequisites and the "
            "course's credit requirements."
        ),
    )

    class Meta:
        ref_name = "CourseSettingsQuery.v2"
