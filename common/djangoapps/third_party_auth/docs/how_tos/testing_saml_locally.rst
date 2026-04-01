Testing SAML Authentication Locally with MockSAML
==================================================

This guide walks through setting up and testing SAML authentication in a local Open edX devstack environment using MockSAML.com as a test Identity Provider (IdP).

Overview
--------

SAML (Security Assertion Markup Language) authentication in Open edX requires three configuration objects to work together:

1. **SAMLConfiguration**: Configures the Service Provider (SP) metadata - entity ID, keys, and organization info
2. **SAMLProviderConfig**: Configures a specific Identity Provider (IdP) connection with metadata URL and attribute mappings
3. **SAMLProviderData**: Stores the IdP's metadata (SSO URL, public key) fetched from the IdP's metadata endpoint

**Critical Requirement**: The SAMLConfiguration object MUST have the slug "default" because this value is hardcoded in the authentication execution path at ``common/djangoapps/third_party_auth/models.py:906``.

Prerequisites
-------------

* Local Open edX devstack running
* Access to Django admin at http://localhost:18000/admin/
* MockSAML.com account (free service for SAML testing)

Step 1: Configure SAMLConfiguration
------------------------------------

The SAMLConfiguration defines your Open edX instance as a SAML Service Provider (SP).

1. Navigate to Django Admin → Third Party Auth → SAML Configurations
2. Click "Add SAML Configuration"
3. Configure with these **required** values:

   ============  ===================================================
   Field         Value
   ============  ===================================================
   Site          localhost:18000
   **Slug**      **default** (MUST be "default" - hardcoded in code)
   Entity ID     https://saml.example.com/entityid
   Enabled       ✓ (checked)
   ============  ===================================================

4. For local testing with MockSAML, you can leave the keys blank.

5. Optionally configure Organization Info (use default or customize):

   .. code-block:: json

      {
        "en-US": {
          "url": "http://localhost:18000",
          "displayname": "Local Open edX",
          "name": "localhost"
        }
      }

6. Click "Save"

Step 2: Configure SAMLProviderConfig
-------------------------------------

The SAMLProviderConfig connects to a specific SAML Identity Provider (MockSAML in this case).

1. Navigate to Django Admin → Third Party Auth → Provider Configuration (SAML IdPs)
2. Click "Add Provider Configuration (SAML IdP)"
3. Configure with these values:

   =========================  ===================================================
   Field                      Value
   =========================  ===================================================
   Name                       Test Localhost (or any descriptive name)
   Slug                       default (to match test URLs)
   Backend Name               tpa-saml
   Entity ID                  https://saml.example.com/entityid
   Metadata Source            https://mocksaml.com/api/saml/metadata
   Site                       localhost:18000
   SAML Configuration         Select the SAMLConfiguration created in Step 1
   Enabled                    ✓ (checked)
   Visible                    ☐ (unchecked for testing)
   Skip hinted login dialog   ✓ (checked - recommended)
   Skip registration form     ✓ (checked - recommended)
   Skip email verification    ✓ (checked - recommended)
   Send to registration first ✓ (checked - recommended)
   =========================  ===================================================

4. Leave all attribute mappings (User ID, Email, Full Name, etc.) blank to use defaults
5. Click "Save"

**Important**: The Entity ID in SAMLProviderConfig MUST match the Entity ID in SAMLConfiguration.

Step 3: Set IdP Data
--------------------

The SAMLProviderData stores metadata from the Identity Provider (MockSAML), create a record with

* **Entity ID**: https://saml.example.com/entityid
* **SSO URL**: https://mocksaml.com/api/saml/sso
* **Public Key**: The IdP's signing certificate
* **Expires At**: Set to 1 year from fetch time


Step 4: Test SAML Authentication
---------------------------------

1. Navigate to: http://localhost:18000/auth/idp_redirect/saml-default
2. You should be redirected to MockSAML.com
3. Complete the authentication on MockSAML - just click "Sign In" with whatever is in the form.
4. You should be redirected back to Open edX
5. If this is a new user, you'll see the registration form
6. After registration, you should be logged in

Expected Behavior
^^^^^^^^^^^^^^^^^

1. Initial redirect to MockSAML (https://mocksaml.com/api/saml/sso)
2. MockSAML displays the login page
3. After authentication, MockSAML POSTs the SAML assertion back to Open edX
4. Open edX validates the assertion and creates/logs in the user
5. User is redirected to the dashboard or registration form (if new user)

Reference Configuration
-----------------------

Here's a summary of a working test configuration:

**SAMLConfiguration** (id=6):

* Site: localhost:18000
* Slug: **default**
* Entity ID: https://saml.example.com/entityid
* Enabled: True

**SAMLProviderConfig** (id=11):

* Name: Test Localhost
* Slug: default
* Entity ID: https://saml.example.com/entityid
* Metadata Source: https://mocksaml.com/api/saml/metadata
* Backend Name: tpa-saml
* Site: localhost:18000
* SAML Configuration: → SAMLConfiguration (id=6)
* Enabled: True

**SAMLProviderData** (id=3):

* Entity ID: https://saml.example.com/entityid
* SSO URL: https://mocksaml.com/api/saml/sso
* Public Key: (certificate from MockSAML metadata)
* Fetched At: 2026-02-27 18:05:40+00:00
* Expires At: 2027-02-27 18:05:41+00:00
* Valid: True

**MockSAML Configuration**:

* SP Entity ID: https://saml.example.com/entityid
* ACS URL: http://localhost:18000/auth/complete/tpa-saml/
* Test User Attributes: email, firstName, lastName, uid
