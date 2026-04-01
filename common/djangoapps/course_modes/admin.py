"""Django admin for course_modes"""


from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.contrib import admin
from django.http.request import QueryDict
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode, CourseModeExpirationConfig
# Technically, we shouldn't be doing this, since verify_student is defined
# in LMS, and course_modes is defined in common.
#
# Once we move the responsibility for administering course modes into
# the Course Admin tool, we can remove this dependency and expose
# verification deadlines as a separate Django model admin.
#
# The admin page will work in both LMS and Studio,
# but the test suite for Studio will fail because
# the verification deadline table won't exist.
from lms.djangoapps.verify_student import models as verification_models
from openedx.core.lib.courses import clean_course_id
from common.djangoapps.util.date_utils import get_time_display

COURSE_MODE_SLUG_CHOICES = [(key, enrollment_mode['display_name'])
                            for key, enrollment_mode in settings.COURSE_ENROLLMENT_MODES.items()]


class CourseModeForm(forms.ModelForm):
    """
    Admin form for adding a course mode.
    """

    class Meta:
        model = CourseMode
        fields = '__all__'

    mode_slug = forms.ChoiceField(choices=COURSE_MODE_SLUG_CHOICES, label=_("Mode"))

    # The verification deadline is stored outside the course mode in the verify_student app.
    # (we used to use the course mode expiration_datetime as both an upgrade and verification deadline).
    # In order to make this transition easier, we include the verification deadline as a custom field
    # in the course mode admin form.  Longer term, we will deprecate the course mode Django admin
    # form in favor of an external Course Administration Tool.
    verification_deadline = forms.SplitDateTimeField(
        label=_("Verification Deadline"),
        required=False,
        help_text=_(
            "OPTIONAL: After this date/time, users will no longer be able to submit photos for verification.  "
            "This appies ONLY to modes that require verification."
        ),
        widget=admin.widgets.AdminSplitDateTime,
    )

    def __init__(self, *args, **kwargs):
        # If args is a QueryDict, then the ModelForm addition request came in as a POST with a course ID string.
        # Change the course ID string to a CourseLocator object by copying the QueryDict to make it mutable.
        if args and 'course' in args[0] and isinstance(args[0], QueryDict):
            args_copy = args[0].copy()
            args_copy['course'] = CourseKey.from_string(args_copy['course'])
            args = [args_copy]

        super().__init__(*args, **kwargs)

        try:
            if self.data.get('course'):
                self.data['course'] = CourseKey.from_string(self.data['course'])
        except AttributeError:
            # Change the course ID string to a CourseLocator.
            # On a POST request, self.data is a QueryDict and is immutable - so this code will fail.
            # However, the args copy above before the super() call handles this case.
            pass

        default_tz = ZoneInfo(settings.TIME_ZONE)

        if self.instance._expiration_datetime:
            # django admin is using default timezone. To avoid time conversion from db to form
            # convert the UTC object to naive and then localize with default timezone.
            _expiration_datetime = self.instance._expiration_datetime.replace(
                tzinfo=None
            )
            self.initial["_expiration_datetime"] = _expiration_datetime.replace(tzinfo=default_tz)
        # Load the verification deadline
        # Since this is stored on a model in verify student, we need to load it from there.
        # We need to munge the timezone a bit to get Django admin to display it without converting
        # it to the user's timezone.  We'll make sure we write it back to the database with the timezone
        # set to UTC later.
        if self.instance.course_id and self.instance.mode_slug in CourseMode.VERIFIED_MODES:
            deadline = verification_models.VerificationDeadline.deadline_for_course(self.instance.course_id)
            self.initial["verification_deadline"] = (
                deadline.replace(tzinfo=default_tz)
                if deadline is not None else None
            )

    def clean_course_id(self):
        """
        Validate the course id
        """
        return clean_course_id(self)

    def clean__expiration_datetime(self):
        """
        Ensure that the expiration datetime we save uses the UTC timezone.
        """
        # django admin saving the date with default timezone to avoid time conversion from form to db
        # changes its tzinfo to UTC
        if self.cleaned_data.get("_expiration_datetime"):
            return self.cleaned_data.get("_expiration_datetime").replace(tzinfo=ZoneInfo("UTC"))

    def clean_verification_deadline(self):
        """
        Ensure that the verification deadline we save uses the UTC timezone.
        """
        if self.cleaned_data.get("verification_deadline"):
            return self.cleaned_data.get("verification_deadline").replace(tzinfo=ZoneInfo("UTC"))

    def clean(self):
        """
        Clean the form fields.
        This is the place to perform checks that involve multiple form fields.
        """
        cleaned_data = super().clean()
        mode_slug = cleaned_data.get("mode_slug")
        upgrade_deadline = cleaned_data.get("_expiration_datetime")
        verification_deadline = cleaned_data.get("verification_deadline")
        expiration_datetime_is_explicit = cleaned_data.get("expiration_datetime_is_explicit")

        if expiration_datetime_is_explicit and upgrade_deadline is None:
            raise forms.ValidationError(
                "An upgrade deadline must be specified when setting Lock upgrade deadline date to True."
            )

        # Allow upgrade deadlines ONLY for the "verified" mode
        # This avoids a nasty error condition in which the upgrade deadline is set
        # for a professional education course before the enrollment end date.
        # When this happens, the course mode expires and students are able to enroll
        # in the course for free.  To avoid this, we explicitly prevent admins from
        # setting an upgrade deadline for any mode except "verified" (which has an upgrade path).
        if upgrade_deadline is not None and mode_slug != CourseMode.VERIFIED:
            raise forms.ValidationError(
                'Only the "verified" mode can have an upgrade deadline.  '
                'For other modes, please set the enrollment end date in Studio.'
            )

        # Verification deadlines are allowed only for verified modes
        if verification_deadline is not None and mode_slug not in CourseMode.VERIFIED_MODES:
            raise forms.ValidationError("Verification deadline can be set only for verified modes.")

        # Verification deadline must be after the upgrade deadline,
        # if an upgrade deadline is set.
        # There are cases in which we might want to set a verification deadline,
        # but not an upgrade deadline (for example, a professional education course that requires verification).
        if verification_deadline is not None:
            if upgrade_deadline is not None and verification_deadline < upgrade_deadline:
                raise forms.ValidationError("Verification deadline must be after the upgrade deadline.")

        return cleaned_data

    def save(self, commit=True):
        """
        Save the form data.
        """
        # Trigger validation so we can access cleaned data
        if self.is_valid():
            course = self.cleaned_data.get("course")
            verification_deadline = self.cleaned_data.get("verification_deadline")
            mode_slug = self.cleaned_data.get("mode_slug")

            # Since the verification deadline is stored in a separate model,
            # we need to handle saving this ourselves.
            # Note that verification deadline can be `None` here if
            # the deadline is being disabled.
            if course is not None and mode_slug in CourseMode.VERIFIED_MODES:
                verification_models.VerificationDeadline.set_deadline(
                    course.id,
                    verification_deadline
                )

        return super().save(commit=commit)


@admin.register(CourseMode)
class CourseModeAdmin(admin.ModelAdmin):
    """Admin for course modes"""
    form = CourseModeForm

    raw_id_fields = ['course']

    fields = (
        'course',
        'mode_slug',
        'mode_display_name',
        'min_price',
        'currency',
        '_expiration_datetime',
        'expiration_datetime_is_explicit',
        'verification_deadline',
        'sku',
        'android_sku',
        'ios_sku',
        'bulk_sku'
    )

    search_fields = ('course__id',)

    list_display = (
        'id',
        'course',
        'mode_slug',
        'min_price',
        'expiration_datetime_custom',
        'sku',
        'android_sku',
        'ios_sku',
        'bulk_sku'
    )

    def expiration_datetime_custom(self, obj):
        """adding custom column to show the expiry_datetime"""
        if obj.expiration_datetime:
            return get_time_display(obj.expiration_datetime, '%B %d, %Y, %H:%M  %p')

    # Display a more user-friendly name for the custom expiration datetime field
    # in the Django admin list view.
    expiration_datetime_custom.short_description = "Upgrade Deadline"


admin.site.register(CourseModeExpirationConfig)
