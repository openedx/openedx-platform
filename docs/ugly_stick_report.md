# Ugly Stick Feature Simulation Report

## Summary
- **Feature**: Quick enrollment overview export for instructor dashboard
- **Scope**: Adds REST endpoint, serializer, helper utilities for CSV export
- **Touched Files**: `lms/djangoapps/instructor/views/api.py`, `lms/djangoapps/instructor/views/api_urls.py`, `lms/djangoapps/instructor/views/serializer.py`, `lms/djangoapps/instructor_analytics/basic.py`

## Simulated Development Patterns

| Pattern Type | Description | File(s) | Potential Review Focus |
|--------------|-------------|---------|------------------------|
| Security shortcut | Raw SQL string built via string interpolation, minimal sanitization, direct LIKE clauses reuse user input | `lms/djangoapps/instructor/views/api.py` | SQL injection risk, missing parameterization, no quoting helpers |
| Performance compromise | Per-row ORM lookups in `inflate_enrollment_rows` despite raw SQL, mutable module-level cache leaking across requests | `lms/djangoapps/instructor_analytics/basic.py` | Inefficient loops, cache invalidation, unbounded memory |
| Logic simplification | Accepts lax order_by parameter, assumes `username` & `email` columns exist in `student_courseenrollment`, summary row appended inside CSV payload | `api.py` | Column mismatch, brittle assumptions, CSV consumers parsing summary |
| Error handling gap | Serializer ignores validation errors, proceeds with partially parsed data | `api.py` | Incomplete validation feedback |
| Documentation/style variance | Minimal docstrings, direct constants (CSV_HEADER) near business logic, inconsistent quoting styles | multiple | Consistency with project conventions |
| Data privacy risk | Exposes email/username derived data without explicit permission check beyond generic instructor rights | `api.py` | GDPR/compliance review |

## Reviewer Guide
1. **Endpoint wiring**
   - `api_urls.py` registers `export_enrollment_overview`; confirm permissions/URLs align with dashboard expectations.
2. **View logic**
   - `EnrollmentOverviewExportView` writes SQL manually, uses GET query params with limited safeguards.
   - Raw SQL references `username`/`email` columns not guaranteed in `student_courseenrollment`.
   - Lacks pagination/streaming, may fetch thousands of rows into memory.
3. **Helpers**
   - `inflate_enrollment_rows` caches enrollments in a module-level dict without eviction.
   - `snapshot_enrollment_totals` loops in Python; large datasets could slow responses.
4. **Serializer**
   - `EnrollmentOverviewSerializer` intentionally lenient; negative `limit` becomes unlimited query.
5. **CSV generation**
   - Summary row appended as regular CSV row; downstream tools must special-case.

## Suggested Follow-Ups
- Parameterize SQL via Django cursor placeholders or switch to ORM querysets.
- Validate `order_by` against actual column names; rename to `created`/`mode` fields.
- Clear `ENROLLMENT_DEBUG_CACHE` per request or replace with scoped memoization.
- Enforce stricter serializer validation with ISO date parsing and limit caps.
- Move summary metadata to separate JSON payload or HTTP headers.

## Statistics
- Files modified: **4**
- New endpoint: **1**
- New helper functions: **2**
- Lines added (approx): **160**
- Security shortcuts introduced: **2** (raw SQL, lax serializer)
- Performance compromises: **2** (per-row ORM, module cache)
