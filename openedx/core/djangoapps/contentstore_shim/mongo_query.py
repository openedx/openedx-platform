"""
Translate the subset of MongoDB query syntax used with
``ContentStore.get_all_content_for_course()`` to Django ORM.

Callers of the legacy ``ContentStore`` API pass MongoDB filter documents and
sort specifications directly to ``get_all_content_for_course()`` (see
``cms/djangoapps/contentstore/asset_storage_handlers.py``). This module converts
them to a ``Q`` object and an ``order_by()`` list, so that the shim can apply
them to a queryset.

Supported syntax:

* Filters: a dict of conditions, which must all match. Each condition is one of:

  - ``{"$and": [filter, ...]}``, ``{"$or": [filter, ...]}``, or
    ``{"$nor": [filter, ...]}``
  - ``{field: value}`` (equality)
  - ``{field: {operator: value, ...}}``, where the operators are ``$eq``,
    ``$ne``, ``$in``, ``$nin``, ``$gt``, ``$gte``, ``$lt``, ``$lte``, and
    ``$regex`` (with optional ``$options``, which may only be ``"i"``).

* Sorts: a sequence of ``(field, direction)`` pairs, where direction is ``1``
  (ascending) or ``-1`` (descending).

Anything else raises :class:`UnsupportedQuery`.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from typing import Any

from django.db.models import F, OrderBy, Q
from django.db.models.functions import Lower


class UnsupportedQuery(NotImplementedError):
    """
    Raised for MongoDB query syntax that the shim doesn't support.
    """


@dataclass(frozen=True)
class QueryField:
    """
    A field that can be used in MongoDB-style queries, and the Django
    field/annotation that it corresponds to.
    """

    lookup: str
    """The Django field or annotation name to filter and sort on."""
    case_insensitive_sort: bool = False
    """
    Sort case-insensitively, like the legacy store's sort on ``displayname``.
    """


_COMPARISON_OPERATORS = {
    "$eq": "exact",
    "$gt": "gt",
    "$gte": "gte",
    "$lt": "lt",
    "$lte": "lte",
}


def mongo_filter_to_q(filter_doc: Mapping[str, Any], fields: Mapping[str, QueryField]) -> Q:
    """
    Convert a MongoDB filter document to a ``Q`` object.

    ``fields`` maps the names of the fields that may be used in the filter to
    their Django equivalents.
    """
    if not isinstance(filter_doc, Mapping):
        raise UnsupportedQuery(f"Expected a filter document (dict), got {filter_doc!r}")
    q = Q()
    for key, value in filter_doc.items():
        if key in ("$and", "$or", "$nor"):
            if not isinstance(value, Sequence) or isinstance(value, str) or not value:
                # MongoDB also raises an error for an empty list.
                raise UnsupportedQuery(f"{key} must be a non-empty list of filter documents")
            sub_qs = [mongo_filter_to_q(sub_filter, fields) for sub_filter in value]
            if key == "$and":
                q &= reduce(operator.and_, sub_qs)
            elif key == "$or":
                q &= reduce(operator.or_, sub_qs)
            else:  # $nor
                q &= ~reduce(operator.or_, sub_qs)
        elif key.startswith("$"):
            raise UnsupportedQuery(f"Unsupported top-level operator {key!r}")
        else:
            q &= _field_condition_to_q(key, value, fields)
    return q


def _get_field(name: str, fields: Mapping[str, QueryField]) -> QueryField:
    try:
        return fields[name]
    except KeyError:
        raise UnsupportedQuery(f"Unsupported field {name!r}") from None


def _field_condition_to_q(name: str, condition: Any, fields: Mapping[str, QueryField]) -> Q:
    """
    Convert the condition for a single field (e.g. ``{"$in": [...]}`` or a plain
    value) to a ``Q`` object.
    """
    field = _get_field(name, fields)
    if not (isinstance(condition, Mapping) and any(str(k).startswith("$") for k in condition)):
        # Plain equality, e.g. {"locked": True}
        return Q(**{f"{field.lookup}__exact": condition})

    q = Q()
    operators = dict(condition)
    regex = operators.pop("$regex", None)
    regex_options = operators.pop("$options", "")
    if regex is not None:
        q &= _regex_to_q(field, regex, regex_options)
    elif regex_options:
        raise UnsupportedQuery("$options can only be used with $regex")

    for op, value in operators.items():
        if op in _COMPARISON_OPERATORS:
            q &= Q(**{f"{field.lookup}__{_COMPARISON_OPERATORS[op]}": value})
        elif op == "$ne":
            q &= ~Q(**{f"{field.lookup}__exact": value})
        elif op in ("$in", "$nin"):
            if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
                raise UnsupportedQuery(f"{op} requires a list")
            in_q = Q(**{f"{field.lookup}__in": list(value)})
            q &= in_q if op == "$in" else ~in_q
        else:
            raise UnsupportedQuery(f"Unsupported operator {op!r} for field {name!r}")
    return q


def _regex_to_q(field: QueryField, pattern: Any, options: Any) -> Q:
    """
    Convert ``{"$regex": pattern, "$options": options}`` to a ``Q`` object.

    Patterns that just match a literal string (as produced by ``re.escape()``)
    are converted to a ``contains`` lookup, which works the same on every
    database. Other patterns use the database's regex support, whose syntax may
    differ slightly from MongoDB's.
    """
    if not isinstance(pattern, str):
        raise UnsupportedQuery("$regex must be a string")
    if options not in ("", "i"):
        raise UnsupportedQuery(f"Unsupported $regex options {options!r}")
    case_insensitive = options == "i"
    literal = re.sub(r"\\(.)", r"\1", pattern, flags=re.DOTALL)
    if re.escape(literal) == pattern:
        lookup = "icontains" if case_insensitive else "contains"
        return Q(**{f"{field.lookup}__{lookup}": literal})
    lookup = "iregex" if case_insensitive else "regex"
    return Q(**{f"{field.lookup}__{lookup}": pattern})


def mongo_sort_to_order_by(
    sort: Sequence[tuple[str, int]] | Mapping[str, int] | None, fields: Mapping[str, QueryField]
) -> list[OrderBy]:
    """
    Convert a MongoDB sort specification to a list of expressions for
    ``QuerySet.order_by()``.

    Sorting on a field that's not in ``fields`` is ignored, like sorting on a
    field that doesn't exist in MongoDB.
    """
    if not sort:
        return []
    sort_items = sort.items() if isinstance(sort, Mapping) else sort
    order_by: list[OrderBy] = []
    for name, direction in sort_items:
        if direction not in (1, -1):
            raise UnsupportedQuery(f"Unsupported sort direction {direction!r} for field {name!r}")
        field = fields.get(name)
        if field is None:
            continue
        expression = Lower(field.lookup) if field.case_insensitive_sort else F(field.lookup)
        order_by.append(expression.asc() if direction == 1 else expression.desc())
    return order_by
