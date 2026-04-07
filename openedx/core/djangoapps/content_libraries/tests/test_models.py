"""
Unit tests for Content Libraries models.
"""


from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from opaque_keys.edx.locator import LibraryLocatorV2, LibraryUsageLocatorV2
from organizations.models import Organization
from pylti1p3.contrib.django.lti1p3_tool_config.models import LtiToolKey

from ..models import ALL_RIGHTS_RESERVED, ContentLibrary, LtiGradedResource, LtiProfile, LtiTool


class ContentLibraryTest(TestCase):
    """
    Tests for ContentLibrary model.
    """

    def _create_library(self, **kwds):
        """
        Create a library model, without a LearningPackage attached to it.
        """
        org = Organization.objects.create(name='foo', short_name='foo')
        return ContentLibrary.objects.create(
            org=org,
            slug='foobar',
            allow_public_learning=False,
            allow_public_read=False,
            license=ALL_RIGHTS_RESERVED,
            **kwds,
        )

    def test_authorize_lti_launch_when_no_library(self):
        """
        Given no library
        When authorize_lti_launch is called
        Then return False
        """
        self.assertFalse(ContentLibrary.objects.exists())
        authorized = ContentLibrary.authorize_lti_launch(
            LibraryLocatorV2(org='foo', slug='foobar'),
            issuer='http://a-fake-issuer',
            client_id='a-fake-client-id')
        self.assertFalse(authorized)

    def test_authorize_lti_launch_when_null(self):
        """
        Given a library WITHOUT an lti tool set
        When authorize_lti_launch is called
        Then return False
        """
        library = self._create_library()
        authorized = ContentLibrary.authorize_lti_launch(
            library.library_key,
            issuer='http://a-fake-issuer',
            client_id='a-fake-client-id')
        self.assertFalse(authorized)

    def test_authorize_lti_launch_when_not_null(self):
        """
        Given a library WITH an lti tool set
        When authorize_lti_launch is called with different issuers
        Then return False
        """
        issuer = 'http://a-fake-issuer'
        client_id = 'a-fake-client-id'
        library = self._create_library()
        library.authorized_lti_configs.add(LtiTool.objects.create(
            issuer=issuer,
            client_id=client_id,
            tool_key=LtiToolKey.objects.create()
        ))
        authorized = ContentLibrary.authorize_lti_launch(
            library.library_key,
            issuer='http://another-fake-issuer',
            client_id='another-fake-client-id')
        self.assertFalse(authorized)

    def test_authorize_lti_launch_when_not_null_and_inactive(self):
        """
        Given a library WITH an lti tool set
        When authorize_lti_launch is called with the same issuers
        And lti tool is inactive
        Then return False
        """
        issuer = 'http://a-fake-issuer'
        client_id = 'a-fake-client-id'
        library = self._create_library()
        library.authorized_lti_configs.add(LtiTool.objects.create(
            issuer=issuer,
            client_id=client_id,
            is_active=False,
            tool_key=LtiToolKey.objects.create()
        ))
        authorized = ContentLibrary.authorize_lti_launch(
            library.library_key,
            issuer='http://another-fake-issuer',
            client_id='another-fake-client-id')
        self.assertFalse(authorized)

    def test_authorize_lti_launch_when_not_null_and_active(self):
        """
        Given a library WITH an lti tool set
        When authorize_lti_launch is called with the same issuers
        And lti tool is active
        Then return True
        """
        issuer = 'http://a-fake-issuer'
        client_id = 'a-fake-client-id'
        library = self._create_library()
        library.authorized_lti_configs.add(LtiTool.objects.create(
            issuer=issuer,
            client_id=client_id,
            is_active=True,  # redudant since it defaults to True
            tool_key=LtiToolKey.objects.create()
        ))
        authorized = ContentLibrary.authorize_lti_launch(
            library.library_key,
            issuer=issuer,
            client_id=client_id)
        self.assertTrue(authorized)


class LtiProfileTest(TestCase):
    """
    LtiProfile model tests.
    """

    def test_get_from_claims_doesnotexists(self):
        with self.assertRaises(LtiProfile.DoesNotExist):
            LtiProfile.objects.get_from_claims(iss='iss', aud='aud', sub='sub')

    def test_get_from_claims_exists(self):
        """
        Given a LtiProfile with iss and sub,
        When get_from_claims()
        Then return the same object.
        """

        iss = 'http://foo.example.com/'
        sub = 'randomly-selected-sub-for-testing'
        aud = 'randomly-selected-aud-for-testing'
        profile = LtiProfile.objects.create(
            platform_id=iss,
            client_id=aud,
            subject_id=sub)

        queried_profile = LtiProfile.objects.get_from_claims(
            iss=iss, aud=aud, sub=sub)

        self.assertEqual(
            queried_profile,
            profile,
            'The queried profile is equal to the profile created.')

    def test_subject_url(self):
        """
        Given a profile
        Then has a valid subject_url.
        """
        iss = 'http://foo.example.com'
        sub = 'randomly-selected-sub-for-testing'
        aud = 'randomly-selected-aud-for-testing'
        expected_url = 'http://foo.example.com/randomly-selected-aud-for-testing/randomly-selected-sub-for-testing'
        profile = LtiProfile.objects.create(
            platform_id=iss,
            client_id=aud,
            subject_id=sub)
        self.assertEqual(expected_url, profile.subject_url)

    def test_create_with_user(self):
        """
        Given a profile without a user
        When save is called
        Then a user is created.
        """

        iss = 'http://foo.example.com/'
        sub = 'randomly-selected-sub-for-testing'
        aud = 'randomly-selected-aud-for-testing'
        profile = LtiProfile.objects.create(
            platform_id=iss,
            client_id=aud,
            subject_id=sub)
        self.assertIsNotNone(profile.user)
        self.assertTrue(
            profile.user.username.startswith('urn:openedx:content_libraries:username:'))

    def test_get_or_create_from_claims(self):
        """
        Given a profile does not exist
        When get or create
        And get or create again
        Then the same profile is returned.
        """
        iss = 'http://foo.example.com/'
        sub = 'randomly-selected-sub-for-testing'
        aud = 'randomly-selected-aud-for-testing'
        self.assertFalse(LtiProfile.objects.exists())
        profile = LtiProfile.objects.get_or_create_from_claims(iss=iss, aud=aud, sub=sub)
        self.assertIsNotNone(profile.user)
        self.assertEqual(iss, profile.platform_id)
        self.assertEqual(sub, profile.subject_id)

        profile_two = LtiProfile.objects.get_or_create_from_claims(iss=iss, aud=aud, sub=sub)
        self.assertEqual(profile_two, profile)

    def test_get_or_create_from_claims_twice(self):
        """
        Given a profile
        When another profile is created
        Then success
        """
        iss = 'http://foo.example.com/'
        aud = 'randomly-selected-aud-for-testing'
        sub_one = 'randomly-selected-sub-for-testing'
        sub_two = 'another-randomly-sub-for-testing'
        self.assertFalse(LtiProfile.objects.exists())
        LtiProfile.objects.get_or_create_from_claims(iss=iss, aud=aud, sub=sub_one)
        LtiProfile.objects.get_or_create_from_claims(iss=iss, aud=aud, sub=sub_two)


class LtiResourceTest(TestCase):
    """
    LtiGradedResource model tests.
    """

    iss = 'fake-iss-for-test'

    sub = 'fake-sub-for-test'

    aud = 'fake-aud-for-test'

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()

    def test_get_from_user_id_when_no_user_then_not_found(self):
        user_id = 0
        with self.assertRaises(LtiGradedResource.DoesNotExist):
            LtiGradedResource.objects.get_from_user_id(user_id)

    def test_get_from_user_id_when_no_profile_then_not_found(self):
        user = get_user_model().objects.create(username='foobar')
        with self.assertRaises(LtiGradedResource.DoesNotExist):
            LtiGradedResource.objects.get_from_user_id(user.pk)

    def test_get_from_user_id_when_profile_then_found(self):
        profile = LtiProfile.objects.get_or_create_from_claims(
            iss=self.iss, aud=self.aud, sub=self.sub)
        LtiGradedResource.objects.create(profile=profile)
        resource = LtiGradedResource.objects.get_from_user_id(profile.user.pk)
        self.assertEqual(profile, resource.profile)

    def test_upsert_from_ags_launch(self):
        """
        Give no graded resource
        When get_or_create_from_launch twice
        Then created at first, retrieved at second.
        """

        resource_id = 'resource-foobar'
        usage_key = 'lb:foo:bar:fooz:barz'
        lineitem = 'http://canvas.docker/api/lti/courses/1/line_items/7'
        resource_endpoint = {
            "lineitem": lineitem,
            "scope": [
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score"
            ],
        }
        resource_link = {
            "id": resource_id,
            "title": "A custom title",
        }

        profile = LtiProfile.objects.get_or_create_from_claims(
            iss=self.iss, aud=self.aud, sub=self.sub)
        block_mock = mock.Mock()
        block_mock.scope_ids.usage_id = LibraryUsageLocatorV2.from_string(usage_key)
        res = LtiGradedResource.objects.upsert_from_ags_launch(
            profile.user, block_mock, resource_endpoint, resource_link)

        self.assertEqual(resource_id, res.resource_id)
        self.assertEqual(lineitem, res.ags_lineitem)
        self.assertEqual(usage_key, str(res.usage_key))
        self.assertEqual(profile, res.profile)

        res2 = LtiGradedResource.objects.upsert_from_ags_launch(
            profile.user, block_mock, resource_endpoint, resource_link)

        self.assertEqual(res, res2)
