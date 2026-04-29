# pylint: disable=missing-module-docstring
from contextlib import contextmanager
from unittest.mock import patch

from edx_toggles.toggles.testutils import override_waffle_flag


@contextmanager
def override_experiment_waffle_flag(flag, active=True, bucket=1):
    """
    Override both the base waffle flag and the experiment bucket value.
    """
    if not active:
        bucket = 0
    with override_waffle_flag(flag, active):
        with patch.object(flag, "get_bucket", return_value=bucket):
            yield
