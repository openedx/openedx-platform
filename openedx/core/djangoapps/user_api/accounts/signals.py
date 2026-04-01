"""
Django Signal related functionality for user_api accounts
"""


from django.dispatch import Signal

# Signal to retire a user from LMS-initiated mailings (course mailings, etc)
# providing_args=["user"]
USER_RETIRE_MAILINGS = Signal()

# Signal to retire LMS critical information
# providing_args=["user", "retired_username", "retired_email"]
USER_RETIRE_LMS_CRITICAL = Signal()

# Signal to retire LMS misc information
# providing_args=["user"]
USER_RETIRE_LMS_MISC = Signal()
