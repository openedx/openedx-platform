# pylint: disable=missing-module-docstring
# Same as vertical,
# But w/o css delimiters between children


from xmodule.vertical_block import VerticalBlock

# HACK: This shouldn't be hard-coded to two types
# OBSOLETE: This obsoletes 'type'
class_priority = ['video', 'problem']


class WrapperBlock(VerticalBlock):
    '''
    Layout block for laying out sub-blocks vertically *w/o* css delimiters.
    '''
    pass  # pylint: disable=unnecessary-pass
