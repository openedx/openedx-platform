"""Executable production rule-builder tests; no platform service mocks needed."""
import json

import pytest

from openedx.core.djangoapps.content.search.access_rules import (
    MAX_FILTER_BYTES,
    SearchScopeTooLarge,
    complete_access_rule,
    library_access_rule,
)


def test_no_access_is_explicit_empty_union():
    assert complete_access_rule([], []) == {"filter": "org IN [] OR access_id IN []"}


def test_org_filter_literal_injection_and_escaping():
    orgs = ['quote" OR access_id IN [123] OR org = "', "slash\\org", "single'quote", "日本語"]
    rule = complete_access_rule(orgs, [])
    encoded = rule["filter"].removeprefix("org IN ").removesuffix(" OR access_id IN []")
    assert json.loads(encoded) == orgs
    assert '\\"' in encoded


@pytest.mark.parametrize(("orgs", "ids"), [([], list(range(1001))), ([str(n) for n in range(1001)], [])])
def test_grants_beyond_1000_never_silently_truncated(orgs, ids):
    with pytest.raises(SearchScopeTooLarge):
        complete_access_rule(orgs, ids)


def test_large_numeric_ids_hit_bytes_budget_below_count_limit():
    with pytest.raises(SearchScopeTooLarge):
        complete_access_rule([], [9_223_372_036_854_775_807] * 500)


def test_large_org_names_hit_bytes_budget():
    with pytest.raises(SearchScopeTooLarge):
        complete_access_rule(["x" * MAX_FILTER_BYTES], [])


def test_scoped_library_filter_is_exact():
    assert library_access_rule("lib:org:target") == {"filter": 'context_key = "lib:org:target"'}


def test_scoped_filter_escapes_untrusted_input():
    key = 'lib:org:target" OR org = "victim'
    expression = library_access_rule(key)["filter"]
    assert json.loads(expression.removeprefix("context_key = ")) == key


def test_scoped_filter_budget_is_bounded():
    with pytest.raises(SearchScopeTooLarge):
        library_access_rule("x" * MAX_FILTER_BYTES)
