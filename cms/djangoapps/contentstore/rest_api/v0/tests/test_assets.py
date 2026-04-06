

"""
Tests for the xblock view of the CMS API. This tests only the view itself,
not the underlying Xblock service.
It checks that the assets_handler method of the Xblock service is called with the expected parameters.
"""
from unittest.mock import MagicMock, patch

from django.core.files import File
from django.http import JsonResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.tests.test_utils import AuthorizeStaffTestCase
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

ASSET_KEY_STRING = "asset-v1:dede+aba+weagi+type@asset+block@_0e37192a-42c4-441e-a3e1-8e40ec304e2e.jpg"
mock_image = MagicMock(file=File)
mock_image.name = "test.jpg"
VERSION = "v0"


class AssetsViewTestCase(AuthorizeStaffTestCase):
    """
    This base class supports tests with the various HTTP methods (GET, POST, PUT, PATCH, and DELETE).
    Tests for each such message are organized by classes that derive from this one (e.g., XblockViewGetTest).
    Each derived class supplies get_test_data() to govern what goes into the body of the HTTP request.
    Each derived class optionally overrides get_url_params() to govern request parameter values.
    Additionally, each derived class supplies send_request() to bring it all together when making a request.
    """

    def get_test_data(self):
        raise NotImplementedError("get_test_data must be implemented by subclasses")

    def get_url_params(self):
        """
        Returns a dictionary of parameters to be used in the url that includes course_id and usage_key_string.
        Override this method if you don't want to use the default values.
        """
        return {"course_id": self.get_course_key_string(), "usage_key_string": ASSET_KEY_STRING}

    def get_url(self, _course_id=None):
        return reverse(
            f"cms.djangoapps.contentstore:{VERSION}:cms_api_update_destroy_assets",
            kwargs=self.get_url_params(),
        )

    def send_request(self, _url, _data):
        raise NotImplementedError("send_request must be implemented by subclasses")

    @patch(
        f"cms.djangoapps.contentstore.rest_api.{VERSION}.views.assets.handle_assets",
        return_value=JsonResponse(
            {
                "locator": ASSET_KEY_STRING,
                "courseKey": AuthorizeStaffTestCase.get_course_key_string(),
            }
        ),
    )
    def make_request(
        self,
        mock_handle_assets,
        run_assertions=None,
        course_id=None,
        data=None,
    ):
        """
        Note that the actual assets handler is mocked out and not used here. Patches used with this method serve to
        test that routing of HTTP requests to the assets handler is correct, that the intended HTTP method has been
        used, that data fed into the handler is as expected, and that data returned by the handler is as expected.
        Inputs and outputs are handled through send_request() polymorphism, to cover all the HTTP methods in a
        common fashion here.
        Validations are through injection of run_assersions().
        """
        url = self.get_url()
        data = self.get_test_data()

        response = self.send_request(url, data)

        # run optional callback method with additional assertions
        if run_assertions:
            run_assertions(
                response=response, mock_handle_assets=mock_handle_assets
            )

        return response


class AssetsViewGetTest(AssetsViewTestCase, ModuleStoreTestCase, APITestCase):
    """
    Test GET operation on xblocks
    """

    def get_url_params(self):
        return {"course_id": self.get_course_key_string()}

    def get_url(self, _course_id=None):
        return reverse(
            f"cms.djangoapps.contentstore:{VERSION}:cms_api_create_retrieve_assets",
            kwargs=self.get_url_params(),
        )

    def get_test_data(self):
        return None

    def assert_assets_handler_called(self, *, mock_handle_assets, response):
        """
        This defines a callback method that is called after the request is made
        and runs additional assertions on the response and mock_handle_assets.
        """
        mock_handle_assets.assert_called_once()
        passed_args = mock_handle_assets.call_args[0][0]

        assert passed_args.method == "GET"
        assert passed_args.path == self.get_url()

    def send_request(self, url, data):
        return self.client.get(url)

    def test_assets_handler_called_with_correct_arguments(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request(  # pylint: disable=no-value-for-parameter
            run_assertions=self.assert_assets_handler_called,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["locator"] == ASSET_KEY_STRING
        assert data["courseKey"] == self.get_course_key_string()


class AssetsViewPostTest(AssetsViewTestCase, ModuleStoreTestCase, APITestCase):
    """
    Test POST operation on xblocks - Create a new xblock for a parent xblock
    """

    def get_url_params(self):
        return {"course_id": self.get_course_key_string()}

    def get_url(self, _course_id=None):
        return reverse(
            f"cms.djangoapps.contentstore:{VERSION}:cms_api_create_retrieve_assets",
            kwargs=self.get_url_params(),
        )

    def get_test_data(self):
        return {
            "file": mock_image,
        }

    def assert_assets_handler_called(self, *, mock_handle_assets, response):
        """
        This defines a callback method that is called after the request is made
        and runs additional assertions on the response and mock_handle_assets.
        """
        mock_handle_assets.assert_called_once()
        passed_args = mock_handle_assets.call_args[0][0]

        course_id = self.get_course_key_string()

        assert passed_args.data.get("file").name == mock_image.name
        assert passed_args.method == "POST"
        assert passed_args.path == self.get_url()

    def send_request(self, url, data):
        return self.client.post(url, data=data, format="multipart")

    def test_assets_handler_called_with_correct_arguments(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request(  # pylint: disable=no-value-for-parameter
            run_assertions=self.assert_assets_handler_called,
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["locator"] == ASSET_KEY_STRING
        assert data["courseKey"] == self.get_course_key_string()


class AssetsViewPutTest(AssetsViewTestCase, ModuleStoreTestCase, APITestCase):
    """
    Test PUT operation on assets - update an asset's locked state
    """

    def get_url_params(self):
        return {"course_id": self.get_course_key_string(), "asset_key_string": ASSET_KEY_STRING}

    def get_test_data(self):
        return {
            "locked": True,
        }

    def assert_assets_handler_called(self, *, mock_handle_assets, response):
        """
        This defines a callback method that is called after the request is made
        and runs additional assertions on the response and mock_handle_assets.
        """
        mock_handle_assets.assert_called_once()
        passed_args = mock_handle_assets.call_args[0][0]

        course_id = self.get_course_key_string()

        assert passed_args.data.get("locked") is True
        assert passed_args.method == "PUT"
        assert passed_args.path == self.get_url()

    def send_request(self, url, data):
        return self.client.put(url, data=data, format="json")

    def test_assets_handler_called_with_correct_arguments(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request(  # pylint: disable=no-value-for-parameter
            run_assertions=self.assert_assets_handler_called,
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["locator"] == ASSET_KEY_STRING
        assert data["courseKey"] == self.get_course_key_string()


class AssetsViewDeleteTest(AssetsViewTestCase, ModuleStoreTestCase, APITestCase):
    """
    Test DELETE asset
    """

    def get_url_params(self):
        return {"course_id": self.get_course_key_string(), "asset_key_string": ASSET_KEY_STRING}

    def get_test_data(self):
        return None

    def assert_assets_handler_called(self, *, mock_handle_assets, response):
        """
        This defines a callback method that is called after the request is made
        and runs additional assertions on the response and mock_handle_assets.
        """
        mock_handle_assets.assert_called_once()
        passed_args = mock_handle_assets.call_args[0][0]

        assert passed_args.method == "DELETE"
        assert passed_args.path == self.get_url()

    def send_request(self, url, data):
        return self.client.delete(url)

    def test_assets_handler_called_with_correct_arguments(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request(  # pylint: disable=no-value-for-parameter
            run_assertions=self.assert_assets_handler_called,
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["locator"] == ASSET_KEY_STRING
        assert data["courseKey"] == self.get_course_key_string()
