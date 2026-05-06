"""Provides a function to convert html to plaintext."""

import logging
from subprocess import PIPE, Popen

log = logging.getLogger(__name__)


def html_to_text(html_message):
    """
    Converts an html message to plaintext.
    Currently uses lynx in a subprocess; should be refactored to
    use something more pythonic.
    """
    process = Popen(  # pylint: disable=consider-using-with
        ['lynx', '-stdin', '-display_charset=UTF-8', '-assume_charset=UTF-8', '-dump'],
        stdin=PIPE,
        stdout=PIPE
    )
    # use lynx to get plaintext
    (plaintext, err_from_stderr) = process.communicate(
        input=html_message.encode('utf-8')
    )

    if err_from_stderr:
        log.info(err_from_stderr)

    return plaintext.decode()
