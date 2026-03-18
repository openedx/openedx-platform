"""
Functions to validate the access in content tagging actions
"""


from openedx_tagging import rules as oel_tagging_rules


def has_view_object_tags_access(user, object_id):
    return user.has_perm(
        "oel_tagging.view_objecttag",
        # The obj arg expects a model, but we are passing an object
        oel_tagging_rules.ObjectTagPermissionItem(taxonomy=None, object_id=object_id),  # type: ignore[arg-type]
    )
