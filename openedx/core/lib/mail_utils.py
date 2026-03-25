"""
Utilities related to mailing.
"""


import textwrap  # noqa: I001

MAX_LINE_LENGTH = 900


def wrap_message(message, width=MAX_LINE_LENGTH):
    """
    RFC 2822 states that line lengths in emails must be less than 998. Some MTA's add newlines to messages if any line
    exceeds a certain limit (the exact limit varies). Sendmail goes so far as to add '!\n' after the 990th character in
    a line. To ensure that messages look consistent this helper function wraps long lines to a conservative length.
    """
    lines = message.split('\n')
    wrapped_lines = [textwrap.fill(
        line, width, expand_tabs=False, replace_whitespace=False, drop_whitespace=False, break_on_hyphens=False
    ) for line in lines]
    wrapped_message = '\n'.join(wrapped_lines)

    return wrapped_message
