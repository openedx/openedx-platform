Extract python api from POST /api/enrollment/v1/enrollment handler
==================================================================

Status
------
Accepted


Context
-------

edx-enterprise bulk enrollment use case currently invokes the mentioned endpoint once per learner + course
which leads to a large number of REST api calls in a short period. It is expensive, and leads to
hitting rate limits when used with our own services/functions within Enterprise.



Decisions
---------

We will copy some of the core enrollment functionality present in the POST handler from
    `POST /api/enrollment/v1/enrollment`
into a non-REST Python method without modify the existing POST handler.

This is so we can call the basic 'create enrollment in LMS' functionality from edx-enterprise without
incurring REST expense. We also do not need to apply the rigorous access and embargo checks present
in the api handler that check the `request` object, since the caller inthis case will be our co-located
code running with the LMS already.

We are not changing the POST handler because it serves various use cases and parameters, as well as
performs authorization checks on request object, none of which are needed and would require careful
and rigorous testing of various enrollment flows, and also introduce risk of regressions if done in a single round of work.

We will add a new function to the `enterprise_support` package (since moved out of edx-platform
and into edx-enterprise as `enterprise.platform_support`) to achieve this.

A few other features of the endpoint are also not needed in order to obtain the functionality needed
to replace the existing POST call:

- any request parsing (since it's not a REST based api)
- any code related to checking access or authorization based on request.user (such as has_api_key_permissions) (since this api will be called from an already secured endpoint within edx-enterprise via bulk enrollment (or other) pathways.
- embargo check (since the caller of this api won't be external or browser-based and there is not request object in scope anymore : the embargo check is based off of the request's IP address)
- `is_active` logic (since we already use is_active=True in the existing post call)
- any logic related to `explicit_linked_enterprise` since we are only using this call to perform a LMS enrollment (student_courseenrollment) and all EnterprisecourseEnrollment specific work will be done after calling this function back in edx-enterprise
- any logic realted to mode changes : this appears to be a use case that is also not relevant to bulk enrollment
- email opt in: modeling afer how we call he endpoint today we don't use this option so not including it


NOTE: No changes will be made to the REST endpoint mentioned, since it also has external customers who may be using
parameters such as `explicit_linked_enterprise` and other pieces of logic too none of which are relevant
to the usage within edx-enterprise and also increase the scope of the task to a great extent.


Consequences
------------

None expected on existing clients, since it's a separate code path from the POST handler.

Benefits: installed apps like edx-enterprise can now avoid making REST API calls to LMS for
enrolling one user in one course run, which is all that is needed for the current use case in bulk enrollment.

Alternatives considered
-----------------------

Write a new endpoint in edx-platform that can handle bulk enrollment in a single REST invocation.
Since an existing bulk_enroll endpoint does exist in edx-platform writing a new one is not sensible.

Use the existing bulk_enroll endpoint that exists in edx-platform somehow. This endpoint cannot be
used as is, and we will still need to handle enterprise-specific concerns.

Add batching and pause between batches in the existing edx-enterprise codebase. This will
avoid the too many requests per unit time. But it involves adding pauses in a request cycle. This
is not ideal for user experience.
