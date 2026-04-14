"""
Common test code for course_experience, like shared base classes.
"""

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.courses import get_course_info_usage_key
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class BaseCourseUpdatesTestCase(SharedModuleStoreTestCase):
    """Base class for working with course updates."""
    @classmethod
    def setUpClass(cls):
        """Set up the simplest course possible."""
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            with cls.store.default_store(ModuleStoreEnum.Type.split):
                cls.course = CourseFactory.create()
                with cls.store.bulk_operations(cls.course.id):
                    # Create a basic course structure
                    chapter = BlockFactory.create(category='chapter', parent_location=cls.course.location)
                    section = BlockFactory.create(category='sequential', parent_location=chapter.location)
                    BlockFactory.create(category='vertical', parent_location=section.location)

    @classmethod
    def setUpTestData(cls):
        """Set up and enroll our fake user in the course."""
        super().setUpTestData()
        cls.user = UserFactory(password=cls.TEST_PASSWORD)
        CourseEnrollment.enroll(cls.user, cls.course.id)

    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def tearDown(self):
        self.remove_course_updates()
        super().tearDown()

    def remove_course_updates(self, user=None, course=None):
        """Remove any course updates in the specified course."""
        user = user or self.user
        course = course or self.course
        updates_usage_key = get_course_info_usage_key(course, 'updates')
        try:
            course_updates = modulestore().get_item(updates_usage_key)
            modulestore().delete_item(course_updates.location, user.id)
        except (ItemNotFoundError, ValueError):
            pass

    def edit_course_update(self, index, content=None, course=None, user=None, date=None, deleted=None):
        """Edits a course update item. Only changes explicitly provided parameters."""
        user = user or self.user
        course = course or self.course
        updates_usage_key = get_course_info_usage_key(course, 'updates')
        course_updates = modulestore().get_item(updates_usage_key)
        for item in course_updates.items:
            if item['id'] == index:
                if date is not None:
                    item['date'] = date
                if content is not None:
                    item['content'] = content
                if deleted is not None:
                    item['status'] = 'deleted' if deleted else 'visible'
                break
        modulestore().update_item(course_updates, user.id)

    def create_course_update(self, content, course=None, user=None, date='December 31, 1999', deleted=False):
        """Creates a test welcome message for the specified course."""
        user = user or self.user
        course = course or self.course
        updates_usage_key = get_course_info_usage_key(course, 'updates')
        try:
            course_updates = modulestore().get_item(updates_usage_key)
        except ItemNotFoundError:
            course_updates = self.create_course_updates_block(course=course, user=user)
        item = {
            'id': len(course_updates.items) + 1,
            'date': date,
            'content': content,
            'status': 'deleted' if deleted else 'visible',
        }
        course_updates.items.append(item)
        modulestore().update_item(course_updates, user.id)
        return item

    def create_course_updates_block(self, course=None, user=None):
        """Create a course updates block."""
        user = user or self.user
        course = course or self.course
        updates_usage_key = get_course_info_usage_key(course, 'updates')
        course_updates = modulestore().create_item(
            user.id,
            updates_usage_key.course_key,
            updates_usage_key.block_type,
            block_id=updates_usage_key.block_id
        )
        course_updates.data = ''
        return course_updates
