"""Code run by pylint before running any tests."""

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs  # noqa: I001

defuse_xml_libs()
