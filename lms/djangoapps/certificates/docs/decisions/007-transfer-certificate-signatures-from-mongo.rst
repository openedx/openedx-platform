Transfer certificate signatures to Credentials IDA
==================================================

Status
------
2022: Accepted

2026: Still unimplemented. Future of Credentials IDA is uncertain. Unclear if we will use this migration, or migrate the signatures somewhere else.

Context
-------
As a part of `Old Mongo Deprecation`_ work, we are planning to remove the support for c4x assets.
After removing the support for c4x assets from the ``StaticContentServer`` middleware, 
the certificate signatures for ``Draft (Old) Mongo`` courses with deprecated IDs will become
unavailable.

.. _`Old Mongo Deprecation`: https://github.com/openedx/public-engineering/issues/62

Decision
--------
We will transfer and store certificate signatures in `Credentials`_ IDA and then use them to render HTML
certificates for ``Draft (Old) Mongo`` courses that have deprecated IDs.

In order to transfer the signatures for ``Draft (Old) Mongo`` and ``Split Mongo`` courses,
it is necessary to complete the next tasks.

* Extend credentials API to provide an option for adding and retrieving signatures.

  * ``CourseCertificateViewSet`` API should provide a possibility to optionally update
    or create signatures for the course certificate.
  
  * ``CourseCertificateViewSet`` API should provide a list of signatures configured for a course certificate.

* Add a new ``OpenEdxPublicSignal`` that is emitted from Studio whenever certificate-related data is changed
  (e.g., certificate image upload, course import). The signature image can be exposed as a URL.

* Create a signal receiver function, that will copy the certificate configuration including signatures images
  to ``CourseCertificate`` model in Credentials using ``CourseCertificateViewSet`` API.

* Add a new management command to copy course certificates configurations,
  including signatures configuration and assets, from MongoDB to Credentials.

* Update render certificate views to get signatures from Credentials
  ``CourseCertificateViewSet`` for ``Draft (Old) Mongo`` courses only.

Example of the certificate configuration stored in MongoDB:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: json

  {
    "certificates": {
      "certificates": [
        {
          "id": 853888039,
          "name": "Name of the certificate",
          "description": "Description of the certificate",
          "is_active": true,
          "version": 1,
          "signatories": [
            {
              "name": "Name",
              "title": "Title",
              "organization": "Organization",
              "signature_image_path": "/c4x/ooniversity/DJ101/asset/png-transparent-circle-white-circle-white-monochrome-black-thumbnail.png",
              "certificate": 853888039,
              "id": 1300534915
            }
          ],
          "course_title": "cert"
        }
      ]
    }
  }


Credentials CourseCertificate model:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* site
* is_active
* signatories

  * Signatory fields:

    * name
    * title
    * organization_name_override
    * image

* title
* course_id
* course_run
* certificate_available_date
* certificate_type
* user_credentials

.. _Credentials: https://github.com/openedx/credentials

Consequences
------------
* After implementing the new ``OpenEdxPublicSignal`` signal receiver,
  every time the course or course certificate is updated,
  certificate configuration including signatures configuration for
  that course will be saved in ``CourseCertificate`` model in Credentials.
* During the certificate rendering, the certificate configuration fetched from MongoDB
  will be merged with signatures configuration retrieved from Credentials. 
* After transferring course certificates configurations from MongoDB to Credentials,
  it would be easier to migrate certificates management from edx-platform to Credentials IDA. 


Alternatives Considered
-----------------------
* `BD-11 Credentials Infrastructure + syncing`_ – suggests migrating course certificates
  configuration to credentials and migrate course certificates frontend to MFEs.

.. _`BD-11 Credentials Infrastructure + syncing`: https://github.com/openedx/credentials/issues/1734

References
---------------
- `Migrate signature assets from MongoDB GridFS to the Credentials IDA <https://github.com/openedx/credentials/issues/1765>`_
- `[DEPR]: DraftModuleStore (Old Mongo Modulestore) <https://github.com/openedx/public-engineering/issues/62>`_
- `Remove the ability to read and write static assets to Old Mongo <https://github.com/openedx/public-engineering/issues/77>`_

