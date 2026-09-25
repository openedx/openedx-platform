"""
Canonical option lists ("values") for course-level Advanced Settings fields
that should be presented as dropdowns in Studio.

These describe the valid choices for enum-like course settings so the Advanced
Settings API can expose them to the frontend, instead of the frontend
hardcoding them. Each list follows the XBlock ``values`` format:
``[{"display_name": ..., "value": ...}, ...]``.

``display_name`` values are plain strings (not wrapped in gettext) for two
reasons: this module is imported by ``xmodule/modulestore/inheritance.py``,
which explicitly forbids importing Django, and the existing enum ``values`` in
``xmodule/course_block.py`` already use plain-string labels. User-facing
translation of these labels is handled by the frontend.

NOTE: ``showanswer`` / ``rerandomize`` / ``show_correctness`` also exist as
problem-level fields in ``xmodule/capa_block.py`` with their own inline
``values``. Those should eventually be migrated to reference these constants so
there is a single source of truth. They are mirrored here (matching the
SHOWANSWER / RANDOMIZATION / ShowCorrectness constants) to keep this module
dependency-light and avoid import cycles.
"""

# Mirrors SHOWANSWER in xmodule/capa_block.py
SHOWANSWER_FIELD_OPTIONS = [
    {"display_name": "Always", "value": "always"},
    {"display_name": "Answered", "value": "answered"},
    {"display_name": "Attempted or Past Due", "value": "attempted"},
    {"display_name": "Closed", "value": "closed"},
    {"display_name": "Finished", "value": "finished"},
    {"display_name": "Correct or Past Due", "value": "correct_or_past_due"},
    {"display_name": "Past Due", "value": "past_due"},
    {"display_name": "Never", "value": "never"},
    {"display_name": "After Some Number of Attempts", "value": "after_attempts"},
    {"display_name": "After All Attempts", "value": "after_all_attempts"},
    {"display_name": "After All Attempts or Correct", "value": "after_all_attempts_or_correct"},
    {"display_name": "Attempted", "value": "attempted_no_past_due"},
]

# Mirrors RANDOMIZATION in xmodule/capa_block.py
RERANDOMIZE_FIELD_OPTIONS = [
    {"display_name": "Always", "value": "always"},
    {"display_name": "On Reset", "value": "onreset"},
    {"display_name": "Never", "value": "never"},
    {"display_name": "Per Student", "value": "per_student"},
]

# Mirrors ShowCorrectness in the xblock.scorable library
SHOW_CORRECTNESS_FIELD_OPTIONS = [
    {"display_name": "Always", "value": "always"},
    {"display_name": "Never", "value": "never"},
    {"display_name": "Past Due", "value": "past_due"},
]

# Mirrors CertificatesDisplayBehaviors in xmodule/data.py
CERTIFICATES_DISPLAY_BEHAVIOR_FIELD_OPTIONS = [
    {"display_name": "End of course", "value": "end"},
    {"display_name": "End of course, with date", "value": "end_with_date"},
    {"display_name": "Immediately upon earning", "value": "early_no_info"},
]
