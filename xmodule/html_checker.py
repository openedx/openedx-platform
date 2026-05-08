# pylint: disable=missing-module-docstring

from lxml import etree


def check_html(html):
    '''
    Check whether the passed in html string can be parsed by lxml.
    Return bool success.
    '''
    parser = etree.HTMLParser()
    try:
        etree.fromstring(html, parser)
        return True
    except Exception:   # pylint: disable=broad-except
        pass
    return False
