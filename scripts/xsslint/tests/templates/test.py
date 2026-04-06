#!/usr/bin/python
# Testing encoding on second line does not cause violation
message = "<script>alert('XSS');</script>"
x = "<string>{}</strong>".format(message)  # noqa: UP032
