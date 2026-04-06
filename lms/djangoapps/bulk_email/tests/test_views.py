"""
Test the bulk email opt out view.
"""

import ddt
import pytest
from django.http import Http404
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.bulk_email.models import Optout
from lms.djangoapps.bulk_email.views import opt_out_email_updates
from lms.djangoapps.discussion.notification_prefs.views import UsernameCipher
from openedx.core.lib.tests import attr
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@attr(shard=1)
@ddt.ddt
@override_settings(SECRET_KEY="test secret key")
class OptOutEmailUpdatesViewTest(ModuleStoreTestCase):
    """
    Check the opt out email functionality.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username="testuser1", email='test@example.com')
        self.course = CourseFactory.create(run='testcourse1', display_name='Test Course Title')
        self.token = UsernameCipher.encrypt('testuser1')
        self.request_factory = RequestFactory()
        self.url = reverse('bulk_email_opt_out', args=[self.token, str(self.course.id)])

        # Ensure we start with no opt-out records
        assert Optout.objects.count() == 0

    def test_opt_out_email_confirm(self):
        """
        Ensure that the default GET view asks for confirmation.
        """
        response = self.client.get(self.url)
        self.assertContains(response, "confirm unsubscribe from")
        assert Optout.objects.count() == 0

    def test_opt_out_email_unsubscribe(self):
        """
        Ensure that the POSTing "confirm" creates the opt-out record.
        """
        response = self.client.post(self.url, {'unsubscribe': True})
        self.assertContains(response, "You have successfully unsubscribed from")
        assert Optout.objects.count() == 1

    def test_opt_out_email_cancel(self):
        """
        Ensure that the POSTing "cancel" does not create the opt-out record
        """
        response = self.client.post(self.url)
        self.assertContains(response, "You have not been unsubscribed from")
        assert Optout.objects.count() == 0

    @ddt.data(
        ("ZOMG INVALID BASE64 CHARS!!!", "base64url", False),
        ("Non-ASCII\xff".encode(), "base64url", False),
        ("D6L8Q01ztywqnr3coMOlq0C3DG05686lXX_1ArEd0ok", "base64url", False),
        ("AAAAAAAAAAA=", "initialization_vector", False),
        ("nMXVK7PdSlKPOovci-M7iqS09Ux8VoCNDJixLBmj", "aes", False),
        ("AAAAAAAAAAAAAAAAAAAAAMoazRI7ePLjEWXN1N7keLw=", "padding", False),
        ("AAAAAAAAAAAAAAAAAAAAACpyUxTGIrUjnpuUsNi7mAY=", "username", False),
        ("_KHGdCAUIToc4iaRGy7K57mNZiiXxO61qfKT08ExlY8=", "course", 'course-v1:testcourse'),
    )
    @ddt.unpack
    def test_unsubscribe_invalid_token(self, token, message, course):
        """
        Make sure that view returns 404 in case token is not valid
        """
        request = self.request_factory.get("dummy")
        with pytest.raises(Http404) as err:
            opt_out_email_updates(request, token, course)
            assert message in err
