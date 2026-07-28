# ruff: noqa: I001
"""
Safer version of lxml.etree.

It overrides some unsafe functions from lxml.etree with safer versions from defusedxml.
It also includes a safer XMLParser.

For processing xml always prefer this over using lxml.etree directly.
"""

# Names are imported into this module so that it can be a stand-in for
# lxml.etree.  The names are not used here, so disable the pylint warning.
# pylint: disable=unused-import, wildcard-import, unused-wildcard-import


from lxml.etree import XMLParser as _XMLParser
from lxml.etree import *  # pylint: disable=redefined-builtin  # noqa: F403
# These private elements are used in some libraries to also defuse xml exploits for their own purposes.
# We need to re-expose them so that the libraries still work.
from lxml.etree import _Comment, _Element, _ElementTree, _Entity, _ProcessingInstruction  # noqa: F401
from .xmlparser import XML, fromstring, parse  # noqa: F401


class XMLParser(_XMLParser):  # pylint: disable=function-redefined
    """
    A safer version of XMLParser which by default disables entity resolution.
    """

    def __init__(self, *args, **kwargs):
        if "resolve_entities" not in kwargs:
            kwargs["resolve_entities"] = False
        super(XMLParser, self).__init__(*args, **kwargs)  # pylint: disable=super-with-arguments  # noqa: UP008
