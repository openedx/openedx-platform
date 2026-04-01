# Based on the lxml example from defusedxml
#
"""lxml.etree protection"""

from __future__ import absolute_import, print_function

import threading

from defusedxml.common import DTDForbidden, EntitiesForbidden, NotSupportedError
from lxml import etree as _etree

LXML3 = _etree.LXML_VERSION[0] >= 3

__origin__ = "lxml.etree"

tostring = _etree.tostring


class RestrictedElement(_etree.ElementBase):
    """A restricted Element class that filters out instances of some classes"""

    __slots__ = ()
    blacklist = (_etree._Entity, _etree._ProcessingInstruction, _etree._Comment)  # pylint: disable=protected-access

    def _filter(self, iterator):
        """Yield only elements not in the blacklist from the given iterator."""
        blacklist = self.blacklist
        for child in iterator:
            if isinstance(child, blacklist):
                continue
            yield child

    def __iter__(self):
        iterator = super().__iter__()
        return self._filter(iterator)

    def iterchildren(self, tag=None, reversed=False):  # pylint: disable=redefined-builtin
        """Iterate over child elements while excluding blacklisted nodes."""
        iterator = super().iterchildren(tag=tag, reversed=reversed)
        return self._filter(iterator)

    def iter(self, tag=None, *tags):  # pylint: disable=keyword-arg-before-vararg
        """Iterate over the element tree excluding blacklisted nodes."""
        iterator = super().iter(tag=tag, *tags)
        return self._filter(iterator)

    def iterdescendants(self, tag=None, *tags):  # pylint: disable=keyword-arg-before-vararg
        """Iterate over descendants while filtering out blacklisted nodes."""
        iterator = super().iterdescendants(tag=tag, *tags)
        return self._filter(iterator)

    def itersiblings(self, tag=None, preceding=False):
        """Iterate over siblings excluding blacklisted node types."""
        iterator = super().itersiblings(tag=tag, preceding=preceding)
        return self._filter(iterator)

    def getchildren(self):
        """Return a list of non-blacklisted child elements."""
        iterator = super().__iter__()
        return list(self._filter(iterator))

    def getiterator(self, tag=None):
        """Iterate over the tree with blacklisted nodes filtered out."""
        iterator = super().getiterator(tag)
        return self._filter(iterator)


class GlobalParserTLS(threading.local):
    """Thread local context for custom parser instances"""

    parser_config = {
        "resolve_entities": False,
    }

    element_class = RestrictedElement

    def create_default_parser(self):
        """Create a secure XMLParser using the restricted element class."""
        parser = _etree.XMLParser(**self.parser_config)
        element_class = self.element_class
        if self.element_class is not None:
            lookup = _etree.ElementDefaultClassLookup(element=element_class)
            parser.set_element_class_lookup(lookup)
        return parser

    def set_default_parser(self, parser):
        """Store a thread-local default XML parser instance."""
        self._default_parser = parser  # pylint: disable=attribute-defined-outside-init

    def get_default_parser(self):
        """Return the thread-local default parser, creating it if missing."""
        parser = getattr(self, "_default_parser", None)
        if parser is None:
            parser = self.create_default_parser()
            self.set_default_parser(parser)
        return parser


_parser_tls = GlobalParserTLS()
get_default_parser = _parser_tls.get_default_parser


def check_docinfo(elementtree, forbid_dtd=False, forbid_entities=True):
    """Check docinfo of an element tree for DTD and entity declarations
    The check for entity declarations needs lxml 3 or newer. lxml 2.x does
    not support dtd.iterentities().
    """
    docinfo = elementtree.docinfo
    if docinfo.doctype:
        if forbid_dtd:
            raise DTDForbidden(docinfo.doctype, docinfo.system_url, docinfo.public_id)
        if forbid_entities and not LXML3:
            # lxml < 3 has no iterentities()
            raise NotSupportedError("Unable to check for entity declarations in lxml 2.x")

    if forbid_entities:
        for dtd in docinfo.internalDTD, docinfo.externalDTD:
            if dtd is None:
                continue
            for entity in dtd.iterentities():
                raise EntitiesForbidden(entity.name, entity.content, None, None, None, None)


def parse(source, parser=None, base_url=None, forbid_dtd=False, forbid_entities=True):
    """Securely parse XML from a source and enforce DTD/entity restrictions."""
    if parser is None:
        parser = get_default_parser()
    elementtree = _etree.parse(source, parser, base_url=base_url)
    check_docinfo(elementtree, forbid_dtd, forbid_entities)
    return elementtree


def fromstring(text, parser=None, base_url=None, forbid_dtd=False, forbid_entities=True):
    """Securely parse XML from a string and validate docinfo."""
    if parser is None:
        parser = get_default_parser()
    rootelement = _etree.fromstring(text, parser, base_url=base_url)
    elementtree = rootelement.getroottree()
    check_docinfo(elementtree, forbid_dtd, forbid_entities)
    return rootelement


XML = fromstring  # pylint: disable=invalid-name


def iterparse(*args, **kwargs):
    """Disabled XML iterparse function that always raises NotSupportedError."""
    raise NotSupportedError("iterparse not available")
