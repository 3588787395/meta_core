#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-indent class bodies in table-driven-panel.js after ES6 conversion."""

import re

filepath = r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\table-driven-panel.js'
with open(filepath, 'r', encoding='utf-8') as f:
    raw = f.read()

lines = raw.split('\n')

# State machine using brace depth:
# depth 0: outside class
# depth 1: inside class body (between methods)
# depth 2+: inside method/constructor body

depth = 0
in_class = False
class_decl_pattern = re.compile(r'^  class \w+ \{$')

output = []

for line in lines:
    # Outside class: output as-is, detect class start
    if not in_class:
        output.append(line)
        if class_decl_pattern.match(line):
            in_class = True
            depth = 1
        continue

    # Inside class
    stripped = line.strip()

    # Blank lines: keep as-is
    if stripped == '':
        output.append(line)
        continue

    # Strip single-line comment for brace counting
    # (verified: no // inside strings in this file)
    code_part = line
    comment_idx = line.find('//')
    if comment_idx >= 0:
        code_part = line[:comment_idx]

    opens = code_part.count('{')
    closes = code_part.count('}')
    new_depth = depth + opens - closes

    if depth == 1:
        # Inside class body (between methods or at method declaration)
        if new_depth >= 2:
            # Method/constructor declaration - keep at 4-space indent
            output.append(line)
        elif new_depth == 1:
            # No depth change - comment/content at class body level
            # Add 2 spaces if not already at 4+ space indent
            if line.startswith('    '):
                output.append(line)
            else:
                output.append('  ' + line)
        elif new_depth == 0:
            # Class closing brace - keep as-is
            output.append(line)
            in_class = False
            depth = 0
            continue
        else:
            # Shouldn't happen (would mean negative depth)
            output.append(line)
    else:
        # Inside method body (depth >= 2)
        if new_depth == 1:
            # Method closing - keep at 4-space indent
            output.append(line)
        else:
            # Body line - add 2 spaces for proper indentation
            output.append('  ' + line)

    depth = new_depth

result = '\n'.join(output)
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(result)

print('Re-indentation complete.')
print('Output lines: ' + str(len(output)))
