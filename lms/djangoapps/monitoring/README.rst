Monitoring
==========

This directory contains utilities for adding a code_owner custom attribute for help with split-ownership of the LMS.

For details on the decision to implement the code_owner custom attribute, see:
lms/djangoapps/monitoring/docs/decisions/0001-monitoring-by-code-owner.rst

Originally, this directory contained the ``CodeOwnerMetricMiddleware``, but that has since been moved to
https://github.com/openedx/edx-django-utils/tree/master/edx_django_utils/monitoring/code_owner
and renamed ``CodeOwnerMonitoringMiddleware``.

This directory continues to contain scripts that can help generate the appropriate ownership mappings for the LMS.
