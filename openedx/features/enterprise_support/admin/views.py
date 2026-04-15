"""
Enterprise support admin views.
"""

from django.contrib import messages
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic.edit import FormView
from enterprise.models import EnterpriseCourseEnrollment

from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAttribute
from openedx.features.enterprise_support.admin.forms import CSVImportForm


class EnrollmentAttributeOverrideView(FormView):
    """
    Learner Enrollment Attribute Override View.
    """
    template_name = 'enterprise_support/admin/enrollment_attributes_override.html'
    form_class = CSVImportForm

    @staticmethod
    def _get_admin_context(request):
        admin_context = {'opts': EnterpriseCourseEnrollment._meta}
        return admin_context

    def get_success_url(self):
        return reverse('admin:enterprise_override_attributes')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_admin_context(self.request))
        return context

    def form_valid(self, form):
        total_records = 0
        error_line_numbers = []
        csv_reader = form.cleaned_data['csv_file']
        for index, record in enumerate(csv_reader):
            total_records += 1
            try:
                course_enrollment = CourseEnrollment.objects.get(
                    user_id=record['lms_user_id'],
                    course_id=record['course_id'],
                )
            except CourseEnrollment.DoesNotExist:
                error_line_numbers.append(str(index + 1))
            else:
                CourseEnrollmentAttribute.objects.update_or_create(
                    enrollment=course_enrollment,
                    namespace='salesforce',
                    name='opportunity_id',
                    defaults={
                        'value': record['opportunity_id'],
                    }
                )

        # if for some reason not a single enrollment updated than do not show success message.
        if len(error_line_numbers) != total_records:
            messages.success(self.request, 'Successfully updated learner enrollment opportunity ids.')

        if error_line_numbers:
            messages.error(
                self.request,
                _(
                    'Enrollment attributes were not updated for records at following line numbers '
                    'in csv because no enrollment found for these records: {error_line_numbers}'
                ).format(error_line_numbers=', '.join(error_line_numbers))
            )

        return super().form_valid(form)
