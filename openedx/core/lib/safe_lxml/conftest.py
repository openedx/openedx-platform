"""
Code run by pytest before running any tests in the safe_lxml directory.
"""
from openedx.core.lib.safe_lxml import defuse_xml_libs

defuse_xml_libs()
