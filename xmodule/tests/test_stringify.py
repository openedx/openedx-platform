"""
Tests stringify functions used in xmodule html
"""

from lxml import etree

from xmodule.stringify import stringify_children


def test_stringify():
    """Test that `stringify_children` correctly concatenates text and child elements."""
    text = 'Hi <div x="foo">there <span>Bruce</span><b>!</b></div>'
    html = f"""<html a="b" foo="bar">{text}</html>"""
    xml = etree.fromstring(html)
    out = stringify_children(xml)
    assert out == text


def test_stringify_again():
    """Test that `stringify_children` handles complex HTML without duplicating content."""
    html = r"""<html name="Voltage Source Answer" >A voltage source is non-linear!
<div align="center">
    <img src="/static/images/circuits/voltage-source.png"/>
    \(V=V_C\)
  </div>
  But it is <a href="http://mathworld.wolfram.com/AffineFunction.html">affine</a>,
  which means linear except for an offset.
  </html>
"""

    html = """<html>A voltage source is non-linear!
  <div align="center">

  </div>
  But it is <a href="http://mathworld.wolfram.com/AffineFunction.html">affine</a>,
  which means linear except for an offset.
  </html>
  """
    xml = etree.fromstring(html)
    out = stringify_children(xml)

    print("output:")
    print(out)

    # Tracking strange content repeating bug
    # Should appear once
    assert out.count("But it is ") == 1
