import re
from typing import Dict, Any, Optional, Tuple


def find_matching_non_leaf_node(
    tree: Dict[str, Any],
    key_pattern: re.Pattern
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Recursively search a COA tree and return:
    (matched_key, matched_subtree)

    Conditions:
    - key matches regex
    - subtree is a dict and has at least one child
    """
    for key, value in tree.items():
        if isinstance(key, str) and key_pattern.search(key):
            if isinstance(value, dict) and len(value) > 0:
                return key, value

        # Recurse only if value is dict
        if isinstance(value, dict):
            result = find_matching_non_leaf_node(value, key_pattern)
            if result:
                return result

    return None

def extract_leaf_nodes(tree: Any) -> list:
    """
    Extract all leaf ledger names from a subtree.
    A leaf is:
    - a key whose value is [] OR
    - an empty dict
    """
    leaves = []

    if isinstance(tree, dict):
        for key, value in tree.items():
            if value == []:
                leaves.append(key)
            elif isinstance(value, dict):
                leaves.extend(extract_leaf_nodes(value))
            elif isinstance(value, list):
                for item in value:
                    leaves.extend(extract_leaf_nodes(item))

    elif isinstance(tree, list):
        for item in tree:
            leaves.extend(extract_leaf_nodes(item))

    return leaves

