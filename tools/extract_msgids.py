#!/usr/bin/env python3
"""Simulate Odoo xml_translate to find exact msgids extracted from view XML."""
from lxml import etree

TRANSLATED_ELEMENTS = {
    'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'code', 'data', 'del', 'dfn', 'em',
    'font', 'i', 'ins', 'kbd', 'keygen', 'mark', 'math', 'meter', 'output',
    'progress', 'q', 'ruby', 's', 'samp', 'small', 'span', 'strong', 'sub',
    'sup', 'time', 'u', 'var', 'wbr', 'text', 'select', 'option',
}

TRANSLATED_ATTRS = {
    'string', 'add-label', 'help', 'sum', 'avg', 'confirm', 'placeholder', 'alt', 'title', 'aria-label',
    'aria-keyshortcuts', 'aria-placeholder', 'aria-roledescription', 'aria-valuetext',
    'value_label', 'data-tooltip', 'label', 'confirm-label', 'confirm-title', 'cancel-label',
}

import re
space_pattern = re.compile(r"[\s﻿]*")

def nonspace(text):
    return bool(text) and not space_pattern.fullmatch(text)

def translatable(node, force_inline=False):
    return (
        node.tag in TRANSLATED_ELEMENTS
        and not any(key.startswith("t-") or key == 'groups' or key.endswith(".translate") for key in node.attrib)
        and all(translatable(child, force_inline) for child in node)
    )

def hastext(node, pos=0, force_inline=False):
    return (
        nonspace(node[pos-1].tail if pos else node.text)
        or (
            pos < len(node)
            and translatable(node[pos], force_inline)
            and (
                any(
                    val and key in TRANSLATED_ATTRS
                    for key, val in node[pos].attrib.items()
                )
                or hastext(node[pos], 0, force_inline)
                or hastext(node, pos + 1, force_inline)
            )
        )
    )

def serialize(node):
    return etree.tostring(node, method='xml', encoding='unicode')

extracted = []

def process(node):
    pos = 0
    while True:
        if hastext(node, pos):
            div = etree.Element('div')
            div.text = (node[pos-1].tail if pos else node.text) or ''
            while pos < len(node) and translatable(node[pos]):
                div.append(node[pos])
            content = serialize(div)[5:-6]
            original = content.strip()
            extracted.append(('TEXT', original))
            # restore
            while len(div) > 0:
                node.insert(pos, div[0])
                pos += 1
        if pos >= len(node):
            break
        process(node[pos])
        pos += 1
    for key, val in node.attrib.items():
        if nonspace(val):
            if key in TRANSLATED_ATTRS:
                extracted.append(('ATTR:' + key, val.strip()))

import sys
if __name__ == '__main__':
    xml_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not xml_path:
        print("usage: extract_msgids.py <xml-file>")
        sys.exit(1)

    tree = etree.parse(xml_path)
    root = tree.getroot()
    # extract just the form view's arch
    for record in root.iter('record'):
        arch = record.find(".//field[@name='arch']")
        if arch is not None:
            view_name_field = record.find(".//field[@name='name']")
            view_name = view_name_field.text if view_name_field is not None else record.get('id', '?')
            print(f"\n=== View: {view_name} ===")
            # arch contains the form/list/etc; iterate its children
            for child in arch:
                process(child)

    for kind, msg in extracted:
        # show all TEXT and ATTR entries
        print(f"\n--- {kind} ---")
        print(repr(msg))
