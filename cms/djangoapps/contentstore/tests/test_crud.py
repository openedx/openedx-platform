"""Tests for CRUD Operations"""


from xmodule import templates
from xmodule.capa_block import ProblemBlock
from xmodule.course_block import CourseBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.exceptions import DuplicateCourseError
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.seq_block import SequenceBlock


class TemplateTests(ModuleStoreTestCase):
    """
    Test finding and using the templates (boilerplates) for xblocks.
    """
    def test_get_templates(self):
        found = templates.all_templates()
        self.assertIsNotNone(found.get('course'))
        self.assertIsNotNone(found.get('about'))
        self.assertIsNotNone(found.get('html'))
        self.assertEqual(len(found.get('course')), 0)
        self.assertEqual(len(found.get('about')), 1)
        self.assertGreaterEqual(len(found.get('html')), 2)

    def test_get_some_templates(self):
        course = CourseFactory.create()
        htmlblock = BlockFactory.create(category="html", parent_location=course.location)

        self.assertEqual(len(SequenceBlock.templates()), 0)
        self.assertGreater(len(htmlblock.templates()), 0)
        self.assertIsNone(SequenceBlock.get_template('doesntexist.yaml'))
        self.assertIsNone(htmlblock.get_template('doesntexist.yaml'))
        self.assertIsNotNone(htmlblock.get_template('announcement.yaml'))

    def test_factories(self):
        test_course = CourseFactory.create(
            org='testx',
            course='course',
            run='2014',
            display_name='fun test course',
            user_id=ModuleStoreEnum.UserID.test,
        )
        self.assertIsInstance(test_course, CourseBlock)
        self.assertEqual(test_course.display_name, 'fun test course')
        course_from_store = self.store.get_course(test_course.id)
        self.assertEqual(course_from_store.id.org, 'testx')
        self.assertEqual(course_from_store.id.course, 'course')
        self.assertEqual(course_from_store.id.run, '2014')

        test_chapter = BlockFactory.create(
            parent_location=test_course.location,
            category='chapter',
            display_name='chapter 1'
        )
        self.assertIsInstance(test_chapter, SequenceBlock)
        # refetch parent which should now point to child
        test_course = self.store.get_course(test_course.id.version_agnostic())
        self.assertIn(test_chapter.location, test_course.children)

        with self.assertRaises(DuplicateCourseError):
            CourseFactory.create(
                org='testx',
                course='course',
                run='2014',
                display_name='fun test course',
                user_id=ModuleStoreEnum.UserID.test,
            )

    def test_temporary_xblocks(self):
        """
        Test create_xblock to create non persisted xblocks
        """
        test_course = CourseFactory.create(
            course='course', run='2014', org='testx',
            display_name='fun test course', user_id=ModuleStoreEnum.UserID.test,
        )

        test_chapter = self.store.create_xblock(
            test_course.runtime, test_course.id, 'chapter', fields={'display_name': 'chapter n'},
            parent_xblock=test_course
        )
        self.assertIsInstance(test_chapter, SequenceBlock)
        self.assertEqual(test_chapter.display_name, 'chapter n')
        self.assertIn(test_chapter, test_course.get_children())

        # test w/ a definition (e.g., a problem)
        test_def_content = '<problem>boo</problem>'
        test_problem = self.store.create_xblock(
            test_course.runtime, test_course.id, 'problem', fields={'data': test_def_content},
            parent_xblock=test_chapter
        )
        self.assertIsInstance(test_problem, ProblemBlock)
        self.assertEqual(test_problem.data, test_def_content)
        self.assertIn(test_problem, test_chapter.get_children())
        test_problem.display_name = 'test problem'
        self.assertEqual(test_problem.display_name, 'test problem')

    def test_delete_course(self):
        test_course = CourseFactory.create(
            org='edu.harvard',
            course='history',
            run='doomed',
            display_name='doomed test course',
            user_id=ModuleStoreEnum.UserID.test,
        )
        BlockFactory.create(
            parent_location=test_course.location,
            category='chapter',
            display_name='chapter 1'
        )

        id_locator = test_course.id.for_branch(ModuleStoreEnum.BranchName.draft)
        # verify it can be retrieved by id
        self.assertIsInstance(self.store.get_course(id_locator), CourseBlock)
        # TODO reenable when split_draft supports getting specific versions
        # guid_locator = test_course.location.course_agnostic()
        # Verify it can be retrieved by guid
        # self.assertIsInstance(self.store.get_item(guid_locator), CourseBlock)
        self.store.delete_course(id_locator, ModuleStoreEnum.UserID.test)
        # Test can no longer retrieve by id.
        self.assertIsNone(self.store.get_course(id_locator))
        # But can retrieve by guid -- same TODO as above
        # self.assertIsInstance(self.store.get_item(guid_locator), CourseBlock)
