"""
Test that we have defused XML.
"""


import pytest
from defusedxml.common import EntitiesForbidden
from lxml import etree

from .xmlparser import fromstring


def test_entities_arent_resolved_exception():
    # Make sure we have disabled entity resolution.
    xml = '<?xml version="1.0"?><!DOCTYPE mydoc [<!ENTITY hi "Hello">]> <root>&hi;</root>'
    parser = etree.XMLParser()
    with pytest.raises(EntitiesForbidden):
        _ = etree.XML(xml, parser=parser)


def test_entities_resolved():
    xml = '<?xml version="1.0"?><!DOCTYPE mydoc [<!ENTITY hi "Hello">]> <root>&hi;</root>'
    parser = etree.XMLParser(resolve_entities=True)
    tree = fromstring(xml, parser=parser, forbid_entities=False)
    pr = etree.tostring(tree)
    assert pr == b'<root>Hello</root>'


def test_entities_arent_resolved():
    # Make sure we have disabled entity resolution.
    xml = '<?xml version="1.0"?><!DOCTYPE mydoc [<!ENTITY hi "Hello">]> <root>&hi;</root>'
    parser = etree.XMLParser()
    tree = fromstring(xml, parser=parser, forbid_entities=False)
    pr = etree.tostring(tree)
    assert pr == b'<root>&hi;</root>'
