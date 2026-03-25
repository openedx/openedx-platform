#!/bin/env python

# Vulture often detects false positives when analyzing a code
# base. If there are particular things you wish to ignore,
# add them below. This file is consumed by
# scripts/dead_code/find-dead-code.sh


from vulture.whitelist_utils import Whitelist  # noqa: I001

view_whitelilst = Whitelist()

# Example:
# view_whitelist.name_of_function_to_whitelist
