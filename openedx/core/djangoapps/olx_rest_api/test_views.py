"""
Test for the OLX REST API app.
"""
from xml.etree import ElementTree

from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import ToyCourseFactory


@skip_unless_cms
class OlxRestApiTestCase(SharedModuleStoreTestCase):
    """
    Test the views (and consequently all the other code) of the OLX REST API.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up a course for use in these tests
        """
        super().setUpClass()
        with cls.store.default_store(ModuleStoreEnum.Type.split):
            cls.course = ToyCourseFactory.create(modulestore=cls.store)
        assert str(cls.course.id).startswith("course-v1:"), "This test is for split mongo course exports only"
        cls.video_key = cls.course.id.make_usage_key('video', 'sample_video')

    def setUp(self):
        """
        Per-test setup
        """
        super().setUp()
        self.user = UserFactory.create(password='edx')
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password='edx')

    # Helper methods:

    def assertXmlEqual(self, xml_str_a: str, xml_str_b: str) -> bool:
        """ Assert that the given XML strings are equal, ignoring attribute order and some whitespace variations. """
        self.assertEqual(  # noqa: PT009
            ElementTree.canonicalize(xml_str_a, strip_text=True),
            ElementTree.canonicalize(xml_str_b, strip_text=True),
        )

    def get_olx_response_for_block(self, block_id):
        return self.client.get(f'/api/olx-export/v1/xblock/{block_id}/')

    # Actual tests:

    def test_no_permission(self):
        """
        A regular user enrolled in the course (but not part of the authoring
        team) should not be able to use the API.
        """
        response = self.get_olx_response_for_block(self.video_key)
        assert response.status_code == 403
        assert response.json()['detail'] ==\
               'You must be a member of the course team in Studio to export OLX using this API.'

    def test_export(self):
        """
        A staff user should be able to use this API to get the OLX of XBlocks in
        the course.
        """
        CourseStaffRole(self.course.id).add_users(self.user)
        response = self.get_olx_response_for_block(self.video_key)
        assert response.status_code == 200
        assert response.json()['root_block_id'] == str(self.video_key)
        blocks = response.json()['blocks']

        # Check the OLX of a video
        self.assertXmlEqual(
            blocks[str(self.video_key)]['olx'],
            '<video youtube="0.75:JMD_ifUUfsU,1.00:OEoXaMPEzfM,1.25:AKqURZnYqpk,1.50:DYpADpL7jAY" '
            'display_name="default" youtube_id_0_75="JMD_ifUUfsU" youtube_id_1_0="OEoXaMPEzfM" '
            'youtube_id_1_25="AKqURZnYqpk" youtube_id_1_5="DYpADpL7jAY"/>\n'
        )

    def test_html_with_static_asset(self):
        """
        Test that HTML gets converted to use CDATA and static assets are
        handled.
        """
        CourseStaffRole(self.course.id).add_users(self.user)

        block_id = str(self.course.id.make_usage_key('html', 'just_img'))
        response = self.get_olx_response_for_block(block_id)
        assert response.status_code == 200
        assert response.json()['root_block_id'] == block_id
        block_data = response.json()['blocks'][block_id]
        self.assertXmlEqual(
            block_data['olx'],
            '''
            <html display_name="Text"><![CDATA[
                <img src="/static/foo_bar.jpg" />
            ]]></html>
            '''
        )
        assert 'static_files' in block_data
        assert 'foo_bar.jpg' in block_data['static_files']
        url = block_data['static_files']['foo_bar.jpg']['url']
        assert url == 'http://testserver/asset-v1:edX+toy+2012_Fall+type@asset+block@foo_bar.jpg'
