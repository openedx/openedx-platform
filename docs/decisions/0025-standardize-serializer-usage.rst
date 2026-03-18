Standardize Serializer Usage Across APIs
========================================

:Status: Proposed
:Date: 2026-03-09
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Serializer standardization for consistency

Context
-------

Many Open edX platform API endpoints manually construct JSON responses using Python dictionaries instead of Django REST Framework (DRF) serializers. This leads to inconsistent schema responses, makes validation errors harder to manage, and creates unpredictable formats that AI and third-party systems struggle with.

Decision
--------

We will standardize all Open edX REST APIs to use **DRF serializers** for request and response handling.

Implementation requirements:

* All API views MUST define explicit serializers for request and response handling.
* Replace manual JSON construction with serializer-based responses.
* Use serializers for both input validation and output formatting.
* Ensure serializers are properly documented with field descriptions and validation rules.
* Maintain backward compatibility for all APIs during migration. While the goal is fully compatible DRF serializers, if that is not possible and we must make a backwards incompatible change, that change MUST be handled by creating a new version of the API and transitioning to that API using the deprecation process.

Relevance in edx-platform
-------------------------

Current patterns that should be migrated:

* **Certificates API** (``/api/certificates/v0/``) constructs JSON manually with nested dictionaries.
* **Enrollment API** endpoints manually build response objects without serializers.
* **Course API** views use hand-coded JSON responses instead of structured serializers.

Code example (target serializer usage)
--------------------------------------

**Example serializer and APIView using DRF best practices:**

.. code-block:: python

   # serializers.py
   from rest_framework import serializers

   class CertificateSerializer(serializers.Serializer):
       username = serializers.CharField(
           help_text="The username of the certificate holder"
       )
       course_id = serializers.CharField(
           help_text="The course identifier"
       )
       status = serializers.CharField(
           help_text="The certificate status (e.g., downloadable, generating)"
       )
       grade = serializers.FloatField(
           help_text="The final grade achieved"
       )

   # views.py
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from rest_framework import status

   class CertificateAPIView(APIView):
       def get(self, request):
           data = {
               "username": "john_doe",
               "course_id": "course-v1:edX+DemoX+1T2024",
               "status": "downloadable",
               "grade": 0.95,
           }
           serializer = CertificateSerializer(data)
           return Response(serializer.data, status=status.HTTP_200_OK)

Consequences
------------

Positive
~~~~~~~~

* Simplifies validation and ensures consistent response contracts.
* Improves AI compatibility through predictable data structures.
* Enables automatic schema generation and documentation.
* Reduces code duplication and maintenance overhead.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires refactoring existing endpoints that manually construct JSON.
* Initial development overhead for creating comprehensive serializers.
* May require updates to existing client code that expects legacy formats.

Alternatives Considered
-----------------------

* **Keep manual JSON construction**: rejected due to inconsistency and maintenance burden.
* **Use DRF defaults only**: rejected because explicit serializers provide better validation and documentation.
* **Use newer ways of managing API responses such as dataclasses or pydantic**: rejected due to complexity and unknowns in transitioning from two existing patterns (manual JSON and DRF serializers) to a third approach. While these python libraries offer better ergonomics, migration would require checking nested serializers, complex validation, and ModelSerializer-heavy endpoints. To move to some new format, we would want to prevent using the basic DRF Serializers any more than we do right now, but preventing new DRF serializers via linting is more complex than anticipated.  This work can be revisited in the future once the platform is a bit more consistent.

Rollout Plan
------------

1. Audit existing endpoints to identify those using manual JSON construction.
2. Create a library of common serializers for shared data structures.
3. Migrate high-impact endpoints first (certificates, enrollment, courses).
4. Update tests to validate serializer-based responses.
5. Update API documentation to reflect new serializer-based contracts.

References
----------

* Open edX REST API Standards: "Serializer Usage" recommendations for API consistency.
