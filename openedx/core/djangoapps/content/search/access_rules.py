"""Bounded, exact tenant-token rules. No incomplete permission sets are issued."""
import json

MAX_ORGS_IN_FILTER = 1_000
MAX_ACCESS_IDS_IN_FILTER = 1_000
MAX_FILTER_BYTES = 4_096
MAX_TENANT_TOKEN_BYTES = 8_192
TOKEN_LIFETIME_SECONDS = 300


class SearchScopeTooLarge(ValueError):
    """The complete authorization set cannot fit in a bounded tenant token."""


def quoted(value: str) -> str:
    """Encode a filter string literal without allowing filter-expression injection."""
    return json.dumps(value, ensure_ascii=False)


def check_filter_size(expression: str) -> dict:
    """Reject oversized filters rather than silently narrowing searchable content."""
    if len(expression.encode("utf-8")) > MAX_FILTER_BYTES:
        raise SearchScopeTooLarge("Search permission filter exceeds its byte budget.")
    return {"filter": expression}


def complete_access_rule(orgs: list[str], access_ids: list[int]) -> dict:
    """Build a complete union of trusted organization roles and individual grants."""
    if len(orgs) > MAX_ORGS_IN_FILTER or len(access_ids) > MAX_ACCESS_IDS_IN_FILTER:
        raise SearchScopeTooLarge("Search permissions require an explicit library scope.")
    expression = f"org IN {json.dumps(orgs, ensure_ascii=False)} OR access_id IN {json.dumps(access_ids)}"
    return check_filter_size(expression)


def library_access_rule(library_key: str) -> dict:
    """Restrict every hit and aggregate to exactly one already-authorized library."""
    return check_filter_size(f"context_key = {quoted(library_key)}")
