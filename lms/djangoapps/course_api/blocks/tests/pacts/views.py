"""
Provider state views needed by pact to setup Provider state for pact verification.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreIsolationMixin
from xmodule.modulestore.tests.factories import CourseFactory


class ProviderState(ModuleStoreIsolationMixin):
    """ Provider State Setup """
    def clean_db(self, user, course_key):
        """ Delete objects from SQL DB and clean mongodb instance """

        CourseEnrollment.objects.filter(course_id=course_key, user=user).delete()

        try:
            self.end_modulestore_isolation()
        except IndexError:
            pass

    def course_setup(self, request):
        """ Setup course data """

        course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')

        self.clean_db(request.user, course_key)
        self.start_modulestore_isolation()

        demo_course = CourseFactory.create(
            org=course_key.org,
            course=course_key.course,
            run=course_key.run,
            display_name="Demonstration Course",
            modulestore=self.store
        )

        CourseEnrollmentFactory.create(user=request.user, course_id=demo_course.id)


@csrf_exempt
@require_POST
def provider_state(request):
    """
    Provider state setup view needed by pact verifier.
    """

    state_setup_mapping = {
        'Blocks data exists for course_id course-v1:edX+DemoX+Demo_Course': ProviderState().course_setup,
    }
    request_body = json.loads(request.body)
    state = request_body.get('state')

    if state in state_setup_mapping:
        print('Setting up provider state for state value: {}'.format(state))
        state_setup_mapping[state](request)

    return JsonResponse({'result': state})
