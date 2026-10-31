"""
Compare a v1 response body with the v2 body that replaces it.

The v1 body is first rewritten by the declared key renames, then both are
flattened to ``{json path: value}`` and diffed. The comparison fails on any
difference that no declared difference covers, and on any declared difference
or rename that did not occur, so the declarations can neither hide a change
nor go stale.
"""

import re

#: Members of an error body that legitimately differ per request.
VOLATILE_FIELDS = {'instance'}


def rename_keys(data, renames):
    """Return ``data`` with every dict key named in ``renames`` replaced, at any depth."""
    if isinstance(data, dict):
        return {renames.get(key, key): rename_keys(value, renames) for key, value in data.items()}
    if isinstance(data, list):
        return [rename_keys(item, renames) for item in data]
    return data


def keys_anywhere(data):
    """Return every dict key that occurs in ``data``, at any depth."""
    if isinstance(data, dict):
        found = set(data)
        for value in data.values():
            found |= keys_anywhere(value)
        return found
    if isinstance(data, list):
        found = set()
        for item in data:
            found |= keys_anywhere(item)
        return found
    return set()


def flatten(data, path=''):
    """Return ``{json path: leaf value}`` for ``data``, leaving out volatile members."""
    flat = {}
    if isinstance(data, dict):
        if not data:
            flat[path] = {}
        for key in sorted(data):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(flatten(data[key], f'{path}.{key}' if path else key))
    elif isinstance(data, list):
        if not data:
            flat[path] = []
        for index, item in enumerate(data):
            flat.update(flatten(item, f'{path}[{index}]'))
    else:
        flat[path] = data
    return flat


def _pattern(declared_path):
    """Return a regex matching ``declared_path``, where ``[*]`` stands for any list index."""
    escaped = re.escape(declared_path).replace(r'\[\*\]', r'\[\d+\]')
    return re.compile(rf'^{escaped}(?:$|[.\[])')


def assert_parity(legacy, new, renames=None, differences=()):
    """
    Fail unless ``new`` equals ``legacy`` apart from the declared renames and differences.

    Arguments:
        legacy: the decoded v1 body.
        new: the decoded v2 body.
        renames (dict): v1 key -> v2 key, applied to the v1 body before diffing.
            Each must name a key present in the v1 body.
        differences: ``(json path, reason)`` pairs, ``[*]`` matching any index.
            Each must match at least one differing path.
    """
    renames = renames or {}
    missing_renames = set(renames) - keys_anywhere(legacy)
    assert not missing_renames, f'declared renames did not occur: {sorted(missing_renames)}'

    flat_legacy = flatten(rename_keys(legacy, renames))
    flat_new = flatten(new)
    changed = {
        path for path in set(flat_legacy) | set(flat_new)
        if flat_legacy.get(path, '<absent>') != flat_new.get(path, '<absent>')
    }
    patterns = [(_pattern(path), path, reason) for path, reason in differences]
    unexpected = sorted(path for path in changed if not any(pattern.match(path) for pattern, _, _ in patterns))
    assert not unexpected, f'undeclared differences: {unexpected}\nv1={legacy}\nv2={new}'
    for pattern, path, reason in patterns:
        assert any(pattern.match(changed_path) for changed_path in changed), (
            f'declared difference did not occur: {path} ({reason})'
        )
