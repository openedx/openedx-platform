"""
Test that various filters are fired for views in the certificates app.
"""
from django.conf import settings
from django.http import HttpResponse
from django.test import override_settings
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CertificateRenderStarted
from rest_framework import status

from lms.djangoapps.certificates.models import CertificateTemplate
from lms.djangoapps.certificates.tests.test_webview_views import CommonCertificatesTestCase
from lms.djangoapps.certificates.utils import get_certificate_url
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase

FEATURES_WITH_CERTS_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_CERTS_ENABLED['CERTIFICATES_HTML_VIEW'] = True


class TestStopCertificateRenderStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, custom_template):  # pylint: disable=arguments-differ
        """Pipeline step that stops the certificate render process."""
        raise CertificateRenderStarted.RenderAlternativeInvalidCertificate(
            "You can't generate a certificate from this site.",
        )


class TestRedirectToPageStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, custom_template):  # pylint: disable=arguments-differ
        """Pipeline step that redirects to another page before rendering the certificate."""
        raise CertificateRenderStarted.RedirectToPage(
            "You can't generate a certificate from this site, redirecting to the correct location.",
            redirect_to="https://certificate.pdf",
        )


class TestRenderCustomResponse(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, custom_template):  # pylint: disable=arguments-differ
        """Pipeline step that returns a custom response when rendering the certificate."""
        response = HttpResponse("Here's the text of the web page.")
        raise CertificateRenderStarted.RenderCustomResponse(
            "You can't generate a certificate from this site.",
            response=response,
        )


class TestCertificateRenderPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, custom_template):  # pylint: disable=arguments-differ
        """
        Pipeline step that gets or creates a new custom template to render instead
        of the original.
        """
        custom_template = self._create_custom_template(mode='honor')
        return {"custom_template": custom_template}

    def _create_custom_template(self, org_id=None, mode=None, course_key=None, language=None):
        """
        Creates a custom certificate template entry in DB.
        """
        template_html = """
            <%namespace name='static' file='static_content.html'/>
            <html>
            <body>
                lang: ${LANGUAGE_CODE}
                course name: ${accomplishment_copy_course_name}
                mode: ${course_mode}
                ${accomplishment_copy_course_description}
                ${twitter_url}
                <img class="custom-logo" src="test-logo.png" />
            </body>
            </html>
        """
        template = CertificateTemplate(
            name='custom template',
            template=template_html,
            organization_id=org_id,
            course_key=course_key,
            mode=mode,
            is_active=True,
            language=language
        )
        template.save()
        return template


@skip_unless_lms
class CertificateFiltersTest(CommonCertificatesTestCase, SharedModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the certificate rendering process.

    This class guarantees that the following filters are triggered during the user's certificate rendering:

    - CertificateRenderStarted
    """

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.views.tests.test_filters.TestCertificateRenderPipelineStep",
                ],
                "fail_silently": False,
            },
        },
        FEATURES=FEATURES_WITH_CERTS_ENABLED,
    )
    def test_certificate_render_filter_executed(self):
        """
        Test whether the student certificate render filter is triggered before the user's
        certificate rendering process.

        Expected result:
            - CertificateRenderStarted is triggered and executes TestCertificateRenderPipelineStep.
            - The certificate renders using the custom template.
        """
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)

        response = self.client.get(test_url)

        self.assertContains(
            response,
            '<img class="custom-logo" src="test-logo.png" />',
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.views.tests.test_filters.TestStopCertificateRenderStep",
                ],
                "fail_silently": False,
            },
        },
        FEATURES=FEATURES_WITH_CERTS_ENABLED,
    )
    def test_certificate_render_invalid(self):
        """
        Test rendering an invalid template after catching RenderAlternativeInvalidCertificate exception.

        Expected result:
            - CertificateRenderStarted is triggered and executes TestStopCertificateRenderStep.
            - The invalid certificate template is rendered.
        """
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)

        response = self.client.get(test_url)

        self.assertContains(response, "Invalid Certificate")
        self.assertContains(response, "Cannot Find Certificate")
        self.assertContains(response, "We cannot find a certificate with this URL or ID number.")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.views.tests.test_filters.TestRedirectToPageStep",
                ],
                "fail_silently": False,
            },
        },
        FEATURES=FEATURES_WITH_CERTS_ENABLED,
    )
    def test_certificate_redirect(self):
        """
        Test redirecting to a new page after catching RedirectToPage exception.

        Expected result:
            - CertificateRenderStarted is triggered and executes TestRedirectToPageStep.
            - The webview response is a redirection.
        """
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)

        response = self.client.get(test_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)
        self.assertEqual("https://certificate.pdf", response.url)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.certificate.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.certificates.views.tests.test_filters.TestRenderCustomResponse",
                ],
                "fail_silently": False,
            },
        },
        FEATURES=FEATURES_WITH_CERTS_ENABLED,
    )
    def test_certificate_render_custom_response(self):
        """
        Test rendering an invalid template after catching RenderCustomResponse exception.

        Expected result:
            - CertificateRenderStarted is triggered and executes TestRenderCustomResponse.
            - The custom response is found in the certificate.
        """
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)

        response = self.client.get(test_url)

        self.assertContains(response, "Here's the text of the web page.")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={},
        FEATURES=FEATURES_WITH_CERTS_ENABLED,
    )
    @with_site_configuration(
        configuration={
            'platform_name': 'My Platform Site',
        },
    )
    def test_certificate_render_without_filter_config(self):
        """
        Test whether the student certificate filter is triggered before the user's
        certificate rendering without affecting its execution flow.

        Expected result:
            - CertificateRenderStarted executes a noop (empty pipeline).
            - The webview response is HTTP_200_OK.
        """
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)

        response = self.client.get(test_url)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertContains(response, "My Platform Site")
