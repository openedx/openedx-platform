"""
Tests for translating MongoDB queries to Django ORM.
"""

import ddt
import pytest
from django.db.models import Q
from django.db.models.functions import Lower
from django.test import SimpleTestCase

from openedx.core.djangoapps.contentstore_shim.mongo_query import (
    QueryField,
    UnsupportedQuery,
    mongo_filter_to_q,
    mongo_sort_to_order_by,
)

FIELDS = {
    "displayname": QueryField("title", case_insensitive_sort=True),
    "contentType": QueryField("content_type"),
    "length": QueryField("size"),
}


@ddt.ddt
class MongoFilterToQTest(SimpleTestCase):
    """
    Tests for mongo_filter_to_q()
    """

    @ddt.data(
        ({}, Q()),
        ({"displayname": "a.png"}, Q(title__exact="a.png")),
        ({"displayname": {"$eq": "a.png"}}, Q(title__exact="a.png")),
        ({"displayname": {"$ne": "a.png"}}, ~Q(title__exact="a.png")),
        ({"length": {"$gt": 1, "$lte": 5}}, Q(size__gt=1) & Q(size__lte=5)),
        ({"contentType": {"$in": ["a/b", "c/d"]}}, Q(content_type__in=["a/b", "c/d"])),
        ({"contentType": {"$nin": ["a/b"]}}, ~Q(content_type__in=["a/b"])),
        ({"displayname": "a", "length": 1}, Q(title__exact="a") & Q(size__exact=1)),
        # Literal regexes (as produced by re.escape()) use "contains":
        ({"displayname": {"$regex": r"a\.png", "$options": "i"}}, Q(title__icontains="a.png")),
        ({"displayname": {"$regex": r"a\ b"}}, Q(title__contains="a b")),
        # Other regexes use the database's regex support:
        ({"displayname": {"$regex": r"^a.*", "$options": "i"}}, Q(title__iregex=r"^a.*")),
        ({"displayname": {"$regex": r"a.png"}}, Q(title__regex=r"a.png")),
        (
            {"$or": [{"displayname": "a"}, {"displayname": "b"}]},
            Q(title__exact="a") | Q(title__exact="b"),
        ),
        (
            {"$and": [{"displayname": "a"}, {"length": 1}]},
            Q(title__exact="a") & Q(size__exact=1),
        ),
        (
            {"$nor": [{"displayname": "a"}, {"displayname": "b"}]},
            ~(Q(title__exact="a") | Q(title__exact="b")),
        ),
        (
            {"$or": [{"contentType": {"$nin": ["a/b"]}}, {"contentType": {"$in": ["c/d"]}}], "length": 2},
            (~Q(content_type__in=["a/b"]) | Q(content_type__in=["c/d"])) & Q(size__exact=2),
        ),
    )
    @ddt.unpack
    def test_filter(self, filter_doc, expected):
        assert mongo_filter_to_q(filter_doc, FIELDS) == expected

    @ddt.data(
        {"filename": "a"},  # unknown field
        {"$where": "true"},  # unsupported top-level operator
        {"$or": []},  # empty $or (also an error in MongoDB)
        {"$or": {"displayname": "a"}},  # not a list
        {"displayname": {"$exists": True}},  # unsupported operator
        {"displayname": {"$regex": "a", "$options": "m"}},  # unsupported regex option
        {"displayname": {"$options": "i"}},  # $options without $regex
        {"contentType": {"$in": "a/b"}},  # $in needs a list
    )
    def test_unsupported(self, filter_doc):
        with pytest.raises(UnsupportedQuery):
            mongo_filter_to_q(filter_doc, FIELDS)


class MongoSortToOrderByTest(SimpleTestCase):
    """
    Tests for mongo_sort_to_order_by()
    """

    def test_sort(self):
        order_by = mongo_sort_to_order_by([("length", -1), ("displayname", 1), ("unknown", 1)], FIELDS)
        assert len(order_by) == 2
        assert order_by[0].descending
        assert order_by[0].expression.name == "size"
        assert not order_by[1].descending
        assert isinstance(order_by[1].expression, Lower)

    def test_dict(self):
        assert len(mongo_sort_to_order_by({"length": 1}, FIELDS)) == 1

    def test_empty(self):
        assert not mongo_sort_to_order_by(None, FIELDS)
        assert not mongo_sort_to_order_by([], FIELDS)

    def test_invalid_direction(self):
        with pytest.raises(UnsupportedQuery):
            mongo_sort_to_order_by([("length", "asc")], FIELDS)
