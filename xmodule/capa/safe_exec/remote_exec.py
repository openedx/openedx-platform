"""
Stub file to preserve backwards compatibility for instances that have explicitly
set CODE_JAIL_REST_SERVICE_REMOTE_EXEC to use this module path.

TODO: Remove this file in a future release using the standard DEPR process.
"""

import warnings

warnings.warn(
    "The 'xmodule.capa.safe_exec.remote_exec' module is deprecated and has been moved. "
    "Please update your CODE_JAIL_REST_SERVICE_REMOTE_EXEC setting to use "
    "'xblocks_contrib.problem.capa.safe_exec.remote_exec.send_safe_exec_request_v0'. "
    "This stub will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

# Wildcard import to expose send_safe_exec_request_v0 and any other attributes
from xblocks_contrib.problem.capa.safe_exec.remote_exec import *  # noqa: F403
