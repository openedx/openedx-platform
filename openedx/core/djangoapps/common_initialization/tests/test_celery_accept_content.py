"""
Regression test for the Celery ``accept_content`` hardening declared in
``openedx/envs/common.py``.
"""
from django.conf import settings
from django.test import SimpleTestCase


class CeleryAcceptContentDefaultTest(SimpleTestCase):
    """
    ``CELERY_ACCEPT_CONTENT`` must be explicitly pinned to JSON-only so
    that a compromised broker cannot push a native-Python-serialization
    message (``application/x-python-serialize``) that the worker would
    decode and execute as arbitrary Python at dispatch time (CWE-502).

    edx-platform already uses the JSON serializer for both tasks and
    results, so this lock is zero-impact on first-party workers.
    """

    def test_accept_content_is_json_only(self):
        accept_content = getattr(settings, 'CELERY_ACCEPT_CONTENT', None)
        assert accept_content is not None, (
            'CELERY_ACCEPT_CONTENT must be explicitly set so it cannot fall '
            'back to a Celery default that includes unsafe serializers.'
        )
        assert list(accept_content) == ['json']

    def test_task_and_result_serializers_still_json(self):
        # Pair assertion: the ACCEPT_CONTENT lock only works as long as the
        # serializer is also JSON, otherwise first-party tasks would fail to
        # deserialize. This guards against a future change that moves to
        # msgpack/yaml without updating ACCEPT_CONTENT.
        assert settings.CELERY_TASK_SERIALIZER == 'json'
        assert settings.CELERY_RESULT_SERIALIZER == 'json'
