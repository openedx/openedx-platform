"""
Annotations Tool Mixin
This file contains global variables and functions used in the various Annotation Tools.
"""

from html.parser import HTMLParser
from os.path import basename, splitext
from urllib.parse import urlparse

from lxml import etree


def get_instructions(xmltree):
    """ Removes <instructions> from the xmltree and returns them as a string, otherwise None. """
    instructions = xmltree.find('instructions')
    if instructions is not None:
        instructions.tag = 'div'
        xmltree.remove(instructions)
        return etree.tostring(instructions, encoding='unicode')
    return None


def get_extension(srcurl):
    """get the extension of a given url """
    if 'youtu' in srcurl:
        return 'video/youtube'
    else:
        disassembled = urlparse(srcurl)
        file_ext = splitext(basename(disassembled.path))[1]
        return 'video/' + file_ext.replace('.', '')


class MLStripper(HTMLParser):  # pylint: disable=abstract-method
    "helper function for html_to_text below"
    def __init__(self):
        HTMLParser.__init__(self)
        self.reset()
        self.fed = []

    def handle_starttag(self, tag, attrs):
        if tag != 'img':
            return
        for attr in attrs:
            if len(attr) >= 2 and attr[0] == 'alt':
                self.fed.append(attr[1])

    def handle_data(self, data):
        """takes the data in separate chunks"""
        self.fed.append(data)

    def handle_entityref(self, name):
        """appends the reference to the body"""
        self.fed.append('&%s;' % name)  # noqa: UP031

    def get_data(self):
        """joins together the seperate chunks into one cohesive string"""
        return ''.join(self.fed)


def html_to_text(html):
    "strips the html tags off of the text to return plaintext"
    htmlstripper = MLStripper()
    htmlstripper.feed(html)
    return htmlstripper.get_data()
