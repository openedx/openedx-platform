How To Use the REST API
#######################

.. How-tos should have a short introduction sentence that captures the user's goal and introduces the steps.

This how-to will help you get setup to be able to make authenticated requests to
the edx-platform REST API.

Assumptions
***********

.. This section should contain a bulleted list of assumptions you have of the
   person who is following the How-to.  The assumptions may link to other
   how-tos if possible.

* You have access to the edx-platform Django Admin (``/admin``) Panel.

* You have a user that you want to make the rest calls as (``UserA``).

* You are familiar with `the basics of HTTP and Rest`_

* For the purposes of this tutorial we'll assume your LMS is located at
  https://lms.example.com

.. _the basics of HTTP and Rest: https://code.tutsplus.com/a-beginners-guide-to-http-and-rest--net-16340t

Steps
*****

.. A task should have 3 - 7 steps.  Tasks with more should be broken down into digestible chunks.

#. Go to https://lms.example.com/admin/oauth2_provider/application/

#. Click :guilabel:`Add Application`

#. Choose "UserA" for the user.

#. Choose ``Confidential`` Client Type

#. Choose "Client Credentials" for the Authorization Grant Type

#. Set a name for your application.

#. Save the ``client_id`` and ``client_secret``.

#. The best way to interact with the edx-platform REST API is by making
   requests using the JWT Authorization header.  Use the ``client_id`` and
   ``client_secret`` to get a JWT token.

   .. code-block:: python

      import base64
      import requests

      client_id = "vovj0AItd9EnrOKjkDli0HpSF9HoooaTY9yueafn"
      # Client secrets should not be exposed in your code, we put it here to
      # make the example more clear.
      client_secret = "a3Fkwr24dfDSlIXt3v3q4Ob41CYQNZyGmtK8Y8ax0srpIa2vJON3OC5Rvj1i1wizsIUv1W1qM1Q2XPeuyjucNixsHXZsuw1dn2B9nH3IyjSvuFb5KoydDvWX8Hx8znqD"

      credential = f"{client_id}:{client_secret}"
      encoded_credential = base64.b64encode(credential.encode("utf-8")).decode("utf-8")

      headers = {"Authorization": f"Basic {encoded_credential}", "Cache-Control": "no-cache"}
      data = {"grant_type": "client_credentials", "token_type": "jwt"}

      token_request = requests.post(
          "http://lms.example.com/oauth2/access_token", headers=headers, data=data
      )
      access_token = token_request.json()["access_token"]


#. The code above will produce a JWT token that you can use to hit any existing
   edx-platform API endpoint.

   .. code-block:: python
      :name: Example, get all courses you're enrolled in.
      :caption: Example, get all of UserA's Enrollments


      enrollment_request = requests.get(
          "http://lms.example.com/api/enrollment/v1/enrollment",
          headers={"Authorization": f"JWT {access_token}"},
      )


.. seealso::

   * :doc:`/concepts/rest_apis`

   * :doc:`/references/auth_code_samples`
