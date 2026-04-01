"""
Classes representing asset metadata.
"""


import json
from datetime import datetime
from zoneinfo import ZoneInfo

import dateutil.parser
from lxml import etree
from opaque_keys.edx.keys import AssetKey, CourseKey


class AssetMetadata:
    """
    Stores the metadata associated with a particular course asset. The asset metadata gets stored
    in the modulestore.
    """

    TOP_LEVEL_ATTRS = ['pathname', 'internal_name', 'locked', 'contenttype', 'thumbnail', 'fields']
    EDIT_INFO_ATTRS = ['curr_version', 'prev_version', 'edited_by', 'edited_by_email', 'edited_on']
    CREATE_INFO_ATTRS = ['created_by', 'created_by_email', 'created_on']
    ATTRS_ALLOWED_TO_UPDATE = TOP_LEVEL_ATTRS + EDIT_INFO_ATTRS
    ASSET_TYPE_ATTR = 'type'
    ASSET_BASENAME_ATTR = 'filename'
    XML_ONLY_ATTRS = [ASSET_TYPE_ATTR, ASSET_BASENAME_ATTR]
    XML_ATTRS = XML_ONLY_ATTRS + ATTRS_ALLOWED_TO_UPDATE + CREATE_INFO_ATTRS

    # Type for assets uploaded by a course author in Studio.
    GENERAL_ASSET_TYPE = 'asset'

    # Asset section XML tag for asset metadata as XML.
    ALL_ASSETS_XML_TAG = 'assets'

    # Individual asset XML tag for asset metadata as XML.
    ASSET_XML_TAG = 'asset'

    # Top-level directory name in exported course XML which holds asset metadata.
    EXPORTED_ASSET_DIR = 'assets'

    # Filename of all asset metadata exported as XML.
    EXPORTED_ASSET_FILENAME = 'assets.xml'

    def __init__(self, asset_id,
                 pathname=None, internal_name=None,
                 locked=None, contenttype=None,
                 thumbnail=None, fields=None,
                 curr_version=None, prev_version=None,
                 created_by=None, created_by_email=None, created_on=None,
                 edited_by=None, edited_by_email=None, edited_on=None,
                 field_decorator=None,):
        """
        Construct a AssetMetadata object.

        Arguments:
            asset_id (AssetKey): Key identifying this particular asset.
            pathname (str): Original path to file at asset upload time.
            internal_name (str): Name, url, or handle for the storage system to access the file.
            locked (bool): If True, only course participants can access the asset.
            contenttype (str): MIME type of the asset.
            thumbnail (str): the internal_name for the thumbnail if one exists
            fields (dict): fields to save w/ the metadata
            curr_version (str): Current version of the asset.
            prev_version (str): Previous version of the asset.
            created_by (int): User ID of initial user to upload this asset.
            created_by_email (str): Email address of initial user to upload this asset.
            created_on (datetime): Datetime of intial upload of this asset.
            edited_by (int): User ID of last user to upload this asset.
            edited_by_email (str): Email address of last user to upload this asset.
            edited_on (datetime): Datetime of last upload of this asset.
            field_decorator (function): used by strip_key to convert OpaqueKeys to the app's understanding.
                Not saved.
        """
        self.asset_id = asset_id if field_decorator is None else field_decorator(asset_id)
        self.pathname = pathname  # Path w/o filename.
        self.internal_name = internal_name
        self.locked = locked
        self.contenttype = contenttype
        self.thumbnail = thumbnail
        self.curr_version = curr_version
        self.prev_version = prev_version
        now = datetime.now(ZoneInfo("UTC"))
        self.edited_by = edited_by
        self.edited_by_email = edited_by_email
        self.edited_on = edited_on or now
        # created_by, created_by_email, and created_on should only be set here.
        self.created_by = created_by
        self.created_by_email = created_by_email
        self.created_on = created_on or now
        self.fields = fields or {}

    def __repr__(self):
        return """AssetMetadata{!r}""".format((
            self.asset_id,
            self.pathname, self.internal_name,
            self.locked, self.contenttype, self.fields,
            self.curr_version, self.prev_version,
            self.created_by, self.created_by_email, self.created_on,
            self.edited_by, self.edited_by_email, self.edited_on,
        ))

    def update(self, attr_dict):
        """
        Set the attributes on the metadata. Any which are not in ATTRS_ALLOWED_TO_UPDATE get put into
        fields.

        Arguments:
            attr_dict: Prop, val dictionary of all attributes to set.
        """
        for attr, val in attr_dict.items():
            if attr in self.ATTRS_ALLOWED_TO_UPDATE:
                setattr(self, attr, val)
            else:
                self.fields[attr] = val

    def to_storable(self):
        """
        Converts metadata properties into a MongoDB-storable dict.
        """
        return {
            'filename': self.asset_id.path,
            'asset_type': self.asset_id.asset_type,
            'pathname': self.pathname,
            'internal_name': self.internal_name,
            'locked': self.locked,
            'contenttype': self.contenttype,
            'thumbnail': self.thumbnail,
            'fields': self.fields,
            'edit_info': {
                'curr_version': self.curr_version,
                'prev_version': self.prev_version,
                'created_by': self.created_by,
                'created_by_email': self.created_by_email,
                'created_on': self.created_on,
                'edited_by': self.edited_by,
                'edited_by_email': self.edited_by_email,
                'edited_on': self.edited_on
            }
        }

    def from_storable(self, asset_doc):
        """
        Fill in all metadata fields from a MongoDB document.

        The asset_id prop is initialized upon construction only.
        """
        if asset_doc is None:
            return
        self.pathname = asset_doc['pathname']
        self.internal_name = asset_doc['internal_name']
        self.locked = asset_doc['locked']
        self.contenttype = asset_doc['contenttype']
        self.thumbnail = asset_doc['thumbnail']
        self.fields = asset_doc['fields']
        self.curr_version = asset_doc['edit_info']['curr_version']
        self.prev_version = asset_doc['edit_info']['prev_version']
        self.created_by = asset_doc['edit_info']['created_by']
        self.created_by_email = asset_doc['edit_info']['created_by_email']
        self.created_on = asset_doc['edit_info']['created_on']
        self.edited_by = asset_doc['edit_info']['edited_by']
        self.edited_by_email = asset_doc['edit_info']['edited_by_email']
        self.edited_on = asset_doc['edit_info']['edited_on']

    def from_xml(self, node):
        """
        Walk the etree XML node and fill in the asset metadata.
        The node should be a top-level "asset" element.
        """
        for child in node:
            qname = etree.QName(child)
            tag = qname.localname
            if tag in self.XML_ATTRS:
                value = child.text
                if tag in self.XML_ONLY_ATTRS:
                    # An AssetLocator is constructed separately from these parts.
                    continue
                elif tag == 'locked':
                    # Boolean.
                    value = True if value == "true" else False  # lint-amnesty, pylint: disable=simplifiable-if-expression
                elif value == 'None':
                    # None.
                    value = None
                elif tag in ('created_on', 'edited_on'):
                    # ISO datetime.
                    value = dateutil.parser.parse(value)
                elif tag in ('created_by', 'edited_by'):
                    # Integer representing user id.
                    value = int(value)
                elif tag == 'fields':
                    # Dictionary.
                    value = json.loads(value)
                setattr(self, tag, value)

    def to_xml(self, node):
        """
        Add the asset data as XML to the passed-in node.
        The node should already be created as a top-level "asset" element.
        """
        for attr in self.XML_ATTRS:
            child = etree.SubElement(node, attr)
            # Get the value.
            if attr == self.ASSET_TYPE_ATTR:
                value = self.asset_id.asset_type
            elif attr == self.ASSET_BASENAME_ATTR:
                value = self.asset_id.path
            else:
                value = getattr(self, attr)

            # Format the value.
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, dict):
                value = json.dumps(value)
            else:
                value = str(value)
            child.text = value

    @staticmethod
    def add_all_assets_as_xml(node, assets):
        """
        Take a list of AssetMetadata objects. Add them all to the node.
        The node should already be created as a top-level "assets" element.
        """
        for asset in assets:
            asset_node = etree.SubElement(node, "asset")
            asset.to_xml(asset_node)


class CourseAssetsFromStorage:
    """
    Wrapper class for asset metadata lists returned from modulestore storage.
    """
    def __init__(self, course_id, doc_id, asset_md):
        """
        Params:
            course_id: Course ID for which the asset metadata is stored.
            doc_id: ObjectId of MongoDB document
            asset_md: Dict with asset types as keys and lists of storable asset metadata as values.
        """
        self.course_id = course_id
        self._doc_id = doc_id
        self.asset_md = asset_md

    @property
    def doc_id(self):
        """
        Returns the ID associated with the MongoDB document which stores these course assets.
        """
        return self._doc_id

    def setdefault(self, item, default=None):
        """
        Provides dict-equivalent setdefault functionality.
        """
        return self.asset_md.setdefault(item, default)

    def __getitem__(self, item):
        return self.asset_md[item]

    def __delitem__(self, item):
        del self.asset_md[item]

    def __len__(self):
        return len(self.asset_md)

    def __setitem__(self, key, value):
        self.asset_md[key] = value

    def get(self, item, default=None):
        """
        Provides dict-equivalent get functionality.
        """
        return self.asset_md.get(item, default)

    def iteritems(self):
        """
        Iterates over the items of the asset dict.
        """
        return self.asset_md.items()

    def items(self):
        """
        Iterates over the items of the asset dict. (Python 3 naming convention)
        """
        return self.iteritems()
