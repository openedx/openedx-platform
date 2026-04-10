"""Assertions related to event validation"""


import json
import pprint


def assert_event_matches(expected, actual, tolerate=None):
    """
    Compare two event dictionaries.

    Fail if any discrepancies exist, and output the list of all discrepancies. The intent is to produce clearer
    error messages than "{ some massive dict } != { some other massive dict }", instead enumerating the keys that
    differ. Produces period separated "paths" to keys in the output, so "context.foo" refers to the following
    structure:

        {
            'context': {
                'foo': 'bar'  # this key, value pair
            }
        }

    The other key difference between this comparison and `assertEquals` is that it supports differing levels of
    tolerance for discrepancies. We don't want to litter our tests full of exact match tests because then anytime we
    add a field to all events, we have to go update every single test that has a hardcoded complete event structure in
    it. Instead we support making partial assertions about structure and content of the event. So if I say my expected
    event looks like this:

        {
            'event_type': 'foo.bar',
            'event': {
                'user_id': 10
            }
        }

    This method will raise an assertion error if the actual event either does not contain the above fields in their
    exact locations in the hierarchy, or if it does contain them but has different values for them. Note that it will
    *not* necessarily raise an assertion error if the actual event contains other fields that are not listed in the
    expected event. For example, the following event would not raise an assertion error:

        {
            'event_type': 'foo.bar',
            'referer': 'http://example.com'
            'event': {
                'user_id': 10
            }
        }

    Note that the extra "referer" field is not considered an error by default.

    The `tolerate` parameter takes a set that allows you to specify varying degrees of tolerance for some common
    eventing related issues. See the `EventMatchTolerates` class for more information about the various flags that are
    supported here.

    Example output if an error is found:

        Unexpected differences found in structs:

        * <path>: not found in actual
        * <path>: <expected_value> != <actual_value> (expected != actual)

        Expected:
            { <expected event }

        Actual:
            { <actual event> }

    "<path>" is a "." separated string indicating the key that differed. In the examples above "event.user_id" would
    refer to the value of the "user_id" field contained within the dictionary referred to by the "event" field in the
    root dictionary.
    """
    differences = get_event_differences(expected, actual, tolerate=tolerate)
    if len(differences) > 0:
        debug_info = [
            '',
            'Expected:',
            block_indent(expected),
            'Actual:',
            block_indent(actual),
            'Tolerating:',
            block_indent(EventMatchTolerates.default_if_not_defined(tolerate)),
        ]
        differences = ['* ' + d for d in differences]
        message_lines = differences + debug_info
        raise AssertionError('Unexpected differences found in structs:\n\n' + '\n'.join(message_lines))


class EventMatchTolerates:
    """
    Represents groups of flags that specify the level of tolerance for deviation between an expected event and an actual
    event.

    These are common event specific deviations that we don't want to handle with special case logic throughout our
    tests.
    """

    # Allow the "event" field to be a string, currently this is the case for all browser events.
    STRING_PAYLOAD = 'string_payload'

    # Allow unexpected fields to exist in the top level event dictionary.
    ROOT_EXTRA_FIELDS = 'root_extra_fields'

    # Allow unexpected fields to exist in the "context" dictionary. This is where new fields that appear in multiple
    # events are most commonly added, so we frequently want to tolerate variation here.
    CONTEXT_EXTRA_FIELDS = 'context_extra_fields'

    # Allow unexpected fields to exist in the "event" dictionary. Typically in unit tests we don't want to allow this
    # type of variance since there are typically only a small number of tests for a particular event type.
    PAYLOAD_EXTRA_FIELDS = 'payload_extra_fields'

    @classmethod
    def default(cls):
        """A reasonable set of tolerated variations."""
        # NOTE: "payload_extra_fields" is deliberately excluded from this list since we want to detect erroneously added
        # fields in the payload by default.
        return {
            cls.STRING_PAYLOAD,
            cls.ROOT_EXTRA_FIELDS,
            cls.CONTEXT_EXTRA_FIELDS,
        }

    @classmethod
    def lenient(cls):
        """Allow all known variations."""
        return cls.default() | {
            cls.PAYLOAD_EXTRA_FIELDS
        }

    @classmethod
    def strict(cls):
        """Allow no variation at all."""
        return frozenset()

    @classmethod
    def default_if_not_defined(cls, tolerates=None):
        """Use the provided tolerance or provide a default one if None was specified."""
        if tolerates is None:
            return cls.default()
        else:
            return tolerates


def assert_events_equal(expected, actual):
    """
    Strict comparison of two events.

    This asserts that every field in the real event exactly matches the expected event.
    """
    assert_event_matches(expected, actual, tolerate=EventMatchTolerates.strict())


def get_event_differences(expected, actual, tolerate=None):
    """Given two events, gather a list of differences between them given some set of tolerated variances."""
    tolerate = EventMatchTolerates.default_if_not_defined(tolerate)

    # Some events store their payload in a JSON string instead of a dict. Comparing these strings can be problematic
    # since the keys may be in different orders, so we parse the string here if we were expecting a dict.
    if EventMatchTolerates.STRING_PAYLOAD in tolerate:
        expected = parse_event_payload(expected)
        actual = parse_event_payload(actual)

    def should_strict_compare(path):
        """
        We want to be able to vary the degree of strictness we apply depending on the testing context.

        Some tests will want to assert that the entire event matches exactly, others will tolerate some variance in the
        context or root fields, but not in the payload (for example).
        """
        if path == [] and EventMatchTolerates.ROOT_EXTRA_FIELDS in tolerate:
            return False
        elif path == ['event'] and EventMatchTolerates.PAYLOAD_EXTRA_FIELDS in tolerate:
            return False
        elif path == ['context'] and EventMatchTolerates.CONTEXT_EXTRA_FIELDS in tolerate:
            return False
        else:
            return True

    return compare_structs(expected, actual, should_strict_compare=should_strict_compare)


def block_indent(text, spaces=4):
    """
    Given a multi-line string, indent every line of it by the given number of spaces.

    If `text` is not a string it is formatted using pprint.pformat.
    """
    return '\n'.join([(' ' * spaces) + l for l in pprint.pformat(text).splitlines()])


def parse_event_payload(event):
    """
    Given an event, parse the 'event' field, if found otherwise 'data' field as a JSON string.

    Note that this may simply return the same event unchanged, or return a new copy of the event with the payload
    parsed. It will never modify the event in place.
    """
    payload_key = 'event' if 'event' in event else 'data'
    if payload_key in event and isinstance(event[payload_key], str):
        event = event.copy()
        try:
            event[payload_key] = json.loads(event[payload_key])
        except ValueError:
            pass
    return event


def compare_structs(expected, actual, should_strict_compare=None, path=None):
    """
    Traverse two structures to ensure that the `actual` structure contains all of the elements within the `expected`
    one.

    Note that this performs a "deep" comparison, descending into dictionaries, lists and ohter collections to ensure
    that the structure matches the expectation.

    If a particular value is not recognized, it is simply compared using the "!=" operator.
    """
    if path is None:
        path = []
    differences = []

    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys = frozenset(list(expected.keys()))
        actual_keys = frozenset(list(actual.keys()))

        for key in expected_keys - actual_keys:
            differences.append(f'{_path_to_string(path + [key])}: not found in actual')

        if should_strict_compare is not None and should_strict_compare(path):
            for key in actual_keys - expected_keys:
                differences.append(f'{_path_to_string(path + [key])}: only defined in actual')

        for key in expected_keys & actual_keys:
            child_differences = compare_structs(expected[key], actual[key], should_strict_compare, path + [key])
            differences.extend(child_differences)

    elif expected != actual:
        differences.append('{path}: {a} != {b} (expected != actual)'.format(  # noqa: UP032
            path=_path_to_string(path),
            a=repr(expected),
            b=repr(actual)
        ))

    return differences


def is_matching_event(expected_event, actual_event, tolerate=None):
    """Return True iff the `actual_event` matches the `expected_event` given the tolerances."""
    return len(get_event_differences(expected_event, actual_event, tolerate=tolerate)) == 0


def _path_to_string(path):
    """Convert a list of path elements into a single path string."""
    return '.'.join(path)
