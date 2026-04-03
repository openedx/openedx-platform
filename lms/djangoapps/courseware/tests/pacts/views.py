"""
Provider state views needed by pact to setup Provider state for pact verification.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.tests.django_utils import ModuleStoreIsolationMixin
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class ProviderState(ModuleStoreIsolationMixin):
    """ Provider State Setup """
    def clean_db(self, user, course_key):   # pylint: disable=unused-argument
        """ clean mongodb instance """

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

        section = BlockFactory.create(
            parent_location=demo_course.location,
            category="chapter",
        )

        BlockFactory.create(
            parent_location=section.location,
            category="sequential",
            display_name="basic_questions",
        )


@csrf_exempt
@require_POST
def provider_state(request):
    """
    Provider state setup view needed by pact verifier.
    """

    state_setup_mapping = {
        'sequence position data exists for course_id course-v1:edX+DemoX+Demo_Course, sequence_id block-v1:edX+DemoX+Demo_Course+type@sequential+block@basic_questions and activeUnitIndex 0': ProviderState().course_setup,  # lint-amnesty, pylint: disable=line-too-long
        'completion block data exists for course_id course-v1:edX+DemoX+Demo_Course, sequence_id block-v1:edX+DemoX+Demo_Course+type@sequential+block@basic_questions and usageId block-v1:edX+DemoX+Demo_Course+type@vertical+block@47dbd5f836544e61877a483c0b75606c': ProviderState().course_setup,  # lint-amnesty, pylint: disable=line-too-long
    }
    request_body = json.loads(request.body)
    state = request_body.get('state')

    if state in state_setup_mapping:
        print('Setting up provider state for state value: {}'.format(state))
        state_setup_mapping[state](request)

    return JsonResponse({'result': state})
