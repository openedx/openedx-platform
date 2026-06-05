LTI Provider
############

This Django app implements the server (provider) side of the `1EdTech LTI
1.1`_ specification, allowing external tools and platforms to launch content
inside the Open edX LMS using OAuth 1.0-signed requests. LTI 1.2 and 1.3
are not supported by this app.

.. _1EdTech LTI 1.1: https://www.imsglobal.org/lti/ltiv1p2/ltiIMGv1p2.html

Security Requirements
*********************

Shared Cache Backend (Required for Multi-Node Deployments)
===========================================================

The LTI provider protects against OAuth replay attacks by storing each
``oauth_nonce`` in Django's cache after it is first seen and rejecting any
subsequent request that presents the same nonce within the validity window
(±5 minutes around the ``oauth_timestamp``).

**This protection only works correctly when all LMS nodes share the same cache
backend.** If you run more than one LMS process or server, you must configure
Django's ``default`` cache to use a shared backend such as Redis or Memcached.
A per-process backend (e.g. Django's built-in ``LocMemCache``) keeps a
separate in-memory store per process, so a replayed request arriving on a
different node will not be detected.

Tutor-based deployments use Redis by default and satisfy this requirement
automatically. For bare-metal or custom deployments, verify that
``CACHES['default']`` is pointed at a shared Redis or Memcached instance.
