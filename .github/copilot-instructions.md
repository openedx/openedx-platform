# Copilot Review Instructions — edx-platform PII Compliance (OEP-30)

## Purpose

When reviewing a pull request, check every **new or modified Django model class**
in the diff for OEP-30 PII annotation compliance. This is required by
[OEP-30](https://open-edx-proposals.readthedocs.io/en/latest/oep-0030-arch-pii-markup-and-auditing.html).

Do not flag files that are not Django model definitions. Focus only on classes
that inherit from `models.Model` (directly or via a parent class like
`TimeStampedModel`, `StatusModel`, etc.).

---

## OEP-30 Annotation Rules

Every Django model class **must** have exactly one of the following in its
class-level docstring:

### Option A — Model contains no PII

```python
class MyModel(models.Model):
    """
    Short description of the model.

    .. no_pii: This model stores no personal data.
    """
```

Or with an explanation (preferred when non-obvious):

```python
class AnonymousUserId(models.Model):
    """
    .. no_pii: We store anonymous_user_ids here, but do not consider them PII under OEP-30.
    """
```

### Option B — Model contains PII

All three lines are required together. Missing any one of them is a violation.

```python
class MyModel(models.Model):
    """
    Short description of the model.

    .. pii: <one-line description of what PII this model holds and how it is retired>
    .. pii_types: <comma-separated list of types from the valid choices below>
    .. pii_retirement: <one retirement method from the valid choices below>
    """
```

---

## Valid Choices (from `.pii_annotations.yml`)

**`pii_types`** — choose one or more, comma-separated:
```
id, name, username, password, location, phone_number, email_address,
birth_date, ip, external_service, biography, gender, sex, image, video, other
```

**`pii_retirement`** — choose exactly one:
```
retained       # PII is kept (document why)
local_api      # Retired via a local LMS API call (e.g. AccountRetirementView)
consumer_api   # Retired via a consumer-facing API
third_party    # Retired via a third-party service (e.g. Segment, Braze)
```

---

## Real Examples From This Codebase

These are taken directly from edx-platform. Use these as the style reference.

### Example 1 — Contains PII, retired via local API
```python
class UserProfile(models.Model):
    """
    This is where we store all the user demographic fields.

    .. pii: Contains many PII fields. Retired in AccountRetirementView.
    .. pii_types: name, location, birth_date, gender, biography, phone_number
    .. pii_retirement: local_api
    """
```

### Example 2 — Contains PII, data is retained
```python
class IDVerificationAttempt(StatusModel):
    """
    Each IDVerificationAttempt represents a Student's attempt to establish
    their identity through one of several methods.

    .. pii: The User's name is stored in this and sub-models
    .. pii_types: name
    .. pii_retirement: retained
    """
```

### Example 3 — Contains PII, retired via third-party
```python
class CourseEnrollmentAllowed(models.Model):
    """
    .. pii: Contains enrolled_email, retired in LMSAccountRetirementView
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
```

### Example 4 — No PII
```python
class UserStanding(models.Model):
    """
    This table contains a student's account's status.

    .. no_pii:
    """
```

### Example 5 — No PII with explanation
```python
class UserAttribute(models.Model):
    """
    Stores arbitrary key/value pairs, currently none are PII.

    .. no_pii: Stores arbitrary key/value pairs, currently none are PII. If that changes, update this annotation.
    """
```

---

## What to Flag in the PR Diff

### Flag as a review comment requiring a fix:

1. **New model class with no annotation at all.**
   Any class inheriting from `models.Model` (or `TimeStampedModel`, `StatusModel`,
   `ConfigurationModel`, `DeletableByUserValue`, etc.) that has no `.. pii:` or
   `.. no_pii:` in its docstring.

2. **New PII-likely field added to an existing model, but `pii_types` not updated.**
   If an existing model already has `.. pii:` but a new field with a PII-likely name
   is added without updating `.. pii_types:`, flag it.
   PII-likely field names include:
   `email`, `username`, `password`, `phone`, `phone_number`, `first_name`,
   `last_name`, `full_name`, `name`, `address`, `mailing_address`, `date_of_birth`,
   `birth_date`, `year_of_birth`, `ip_address`, `biography`, `bio`, `gender`,
   `profile_image`, `avatar`, `location`, `city`, `country`, `state`,
   `social_security`, `national_id`, `external_id`, `secondary_email`.

3. **Incomplete annotation block.** `.. pii:` present but `.. pii_types:` or
   `.. pii_retirement:` is missing — all three lines are required together.

### Do NOT flag:

- Abstract base models that document PII in their docstring (sub-models may
  reference the parent with `"stored in the parent model"`).
- Models already in `.annotation_safe_list.yml` (third-party models).
- Non-model Python classes, functions, views, serializers, or utility files.
- Fields that already have annotation coverage and haven't changed.

---

## How to Post the Inline Suggestion

Post an **inline review comment** on the model's class definition line or
docstring. Use this format:

> **[PII Compliance]** This model is missing an OEP-30 annotation.
>
> Since `phone_number` is a PII field, add one of the following to the class docstring:
>
> **If this model stores PII:**
> ```
> .. pii: Contains user phone number for contact purposes.
> .. pii_types: phone_number
> .. pii_retirement: local_api
> ```
>
> **If this model stores no PII:**
> ```
> .. no_pii: This model stores no personal data.
> ```
>
> Run `make pii_check` locally to verify. See [OEP-30] and `.pii_annotations.yml` for valid choices.

If `pii_types` needs updating on an existing annotated model:

> **[PII Compliance]** The new `secondary_email` field appears to contain PII, but
> `.. pii_types:` was not updated.
>
> Consider adding `email_address` to the existing `.. pii_types:` line.
> Run `make pii_check` to verify.

---

## Retirement Flow Note

If the modified model appears to be referenced in `scripts/user_retirement/`
(look for its class name in the diff context or in `utils/edx_api.py`), and a
new PII field is added, also note:

> **[PII Compliance - Retirement]** If `<ModelName>` participates in the user
> retirement pipeline, verify that the new `<field_name>` field is covered in
> `scripts/user_retirement/utils/edx_api.py` or the relevant LMS management command.

---

## Reference Files in This Repository

| File | Purpose |
|---|---|
| `.pii_annotations.yml` | Annotation schema — valid `pii_types` and `pii_retirement` values |
| `.annotation_safe_list.yml` | Third-party models already classified (skip these) |
| `scripts/user_retirement/utils/edx_api.py` | Main retirement API handler |
| `openedx/core/djangoapps/user_api/management/` | LMS-side retirement management commands |
| `common/djangoapps/student/models/user.py` | Reference: `UserProfile` — canonical PII annotation example |
| `lms/djangoapps/verify_student/models.py` | Reference: `IDVerificationAttempt` — inherited PII example |
