"""Tests for the lms module itself."""


import logging
import mimetypes

from django.conf import settings  # lint-amnesty, pylint: disable=unused-import  # noqa: F401
from django.test import TestCase

log = logging.getLogger(__name__)


class LmsModuleTests(TestCase):
    """
    Tests for lms module itself.
    """

    def test_new_mimetypes(self):
        extensions = ['eot', 'otf', 'ttf', 'woff']
        for extension in extensions:
            mimetype, _ = mimetypes.guess_type('test.' + extension)
            assert mimetype is not None

    def test_api_docs(self):
        """
        Tests that requests to the `/api-docs/` endpoint do not raise an exception.
        """
        response = self.client.get('/api-docs/')
        assert response.status_code == 200

    def test_sec_s3_public_read_lms_production_acl_is_private_regression(self):
        """
        Regression test: LMS production settings must default S3 objects to
        private. Read URLs are produced via ``storage.url()`` which generates
        pre-signed URLs (AWS_QUERYSTRING_AUTH is True), so no read path
        depends on a public ACL. Preventing regression of an LMS-only
        divergence from CMS where objects were historically public-read.
        """
        import os
        lms_production_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'lms', 'envs', 'production.py',
        )
        with open(lms_production_path, encoding='utf-8') as handle:
            source = handle.read()
        assert "AWS_DEFAULT_ACL = 'private'" in source, (
            "LMS production AWS_DEFAULT_ACL must be 'private' "
            "to match CMS and avoid publishing uploaded content. "
            "See the security advisory on S3 object ACLs."
        )
        assert "AWS_DEFAULT_ACL = 'public-read'" not in source, (
            "LMS production AWS_DEFAULT_ACL must not be 'public-read'."
        )

    def test_sec_s3_public_read_lms_matches_cms_regression(self):
        """
        Regression test: LMS and CMS must declare the same
        ``AWS_DEFAULT_ACL`` in their production settings so the two cannot
        drift again.
        """
        import os
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)),
        )
        lms_path = os.path.join(repo_root, 'lms', 'envs', 'production.py')
        cms_path = os.path.join(repo_root, 'cms', 'envs', 'production.py')

        def _extract_acl(path):
            with open(path, encoding='utf-8') as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith('AWS_DEFAULT_ACL = '):
                        return stripped
            return None

        lms_acl = _extract_acl(lms_path)
        cms_acl = _extract_acl(cms_path)
        assert lms_acl is not None, "LMS production.py must set AWS_DEFAULT_ACL"
        assert cms_acl is not None, "CMS production.py must set AWS_DEFAULT_ACL"
        assert lms_acl == cms_acl, (
            f"LMS and CMS AWS_DEFAULT_ACL must match: "
            f"lms={lms_acl!r} cms={cms_acl!r}"
        )
