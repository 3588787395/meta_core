#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Temporary script to convert TableDrivenPanel and TableDrivenForm to ES6 Class."""

import re
import sys

filepath = r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\table-driven-panel.js'
with open(filepath, 'r', encoding='utf-8') as f:
    raw = f.read()

lines = raw.split('\n')

# Patterns (2-space indent)
ctor_pattern = re.compile(r'^  function (TableDrivenPanel|TableDrivenForm)\((.*?)\) \{$')
proto_pattern = re.compile(r'^  (TableDrivenPanel|TableDrivenForm)\.prototype\.(\w+) = function \((.*?)\) \{$')
static_prop_pattern = re.compile(r'^  (TableDrivenPanel)\.(_tdx\w+) = (.*);$')

# Find constructor declarations and their closings (first `  }` after declaration)
ctor_lines = {}        # line_index -> (classname, args)
ctor_close_lines = {}  # line_index -> classname
for i, line in enumerate(lines):
    m = ctor_pattern.match(line)
    if m:
        classname = m.group(1)
        args = m.group(2)
        ctor_lines[i] = (classname, args)
        # Find closing `  }` (exactly 2-space indent, no semicolon)
        for j in range(i + 1, len(lines)):
            if lines[j] == '  }':
                ctor_close_lines[j] = classname
                break

# Find prototype method declarations and their closings (first `  };` after declaration)
proto_lines = {}         # line_index -> (classname, methodname, args)
method_close_lines = {}  # line_index -> classname
for i, line in enumerate(lines):
    m = proto_pattern.match(line)
    if m:
        classname = m.group(1)
        methodname = m.group(2)
        args = m.group(3)
        proto_lines[i] = (classname, methodname, args)
        # Find closing `  };` (exactly 2-space indent, with semicolon)
        for j in range(i + 1, len(lines)):
            if lines[j] == '  };':
                method_close_lines[j] = classname
                break

# Find static properties
static_prop_lines = {}  # line_index -> original line
for i, line in enumerate(lines):
    m = static_prop_pattern.match(line)
    if m:
        static_prop_lines[i] = line

# Find the last method close for each class
last_method_close = {}  # classname -> line_index
for close_idx, classname in method_close_lines.items():
    if classname not in last_method_close or close_idx > last_method_close[classname]:
        last_method_close[classname] = close_idx

# Build output
output = []
pending_static_props = []

for i, line in enumerate(lines):
    # Constructor declaration -> class + constructor
    if i in ctor_lines:
        classname, args = ctor_lines[i]
        output.append('  class ' + classname + ' {')
        output.append('    constructor(' + args + ') {')
        continue

    # Constructor closing -> close constructor (stay in class)
    if i in ctor_close_lines:
        output.append('    }')
        continue

    # Static property -> skip (will add after class)
    if i in static_prop_lines:
        pending_static_props.append(line)
        continue

    # Prototype method declaration -> class method
    if i in proto_lines:
        classname, methodname, args = proto_lines[i]
        output.append('    ' + methodname + '(' + args + ') {')
        continue

    # Method closing -> close method, maybe close class
    if i in method_close_lines:
        output.append('    }')
        classname = method_close_lines[i]
        if i == last_method_close[classname]:
            # Close the class
            output.append('  }')
            # Add static props (if any)
            if pending_static_props:
                output.append('')  # blank line separator
                for prop in pending_static_props:
                    output.append(prop)
                pending_static_props = []
        continue

    # Regular line - keep as-is
    output.append(line)

# Close class if still open (safety)
if pending_static_props:
    for prop in pending_static_props:
        output.append(prop)

result = '\n'.join(output)
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(result)

# Verification output
print('Conversion complete.')
print('Original lines: ' + str(len(lines)))
print('Output lines:   ' + str(len(output)))
print('Constructor declarations: ' + str(ctor_lines))
print('Constructor closings: ' + str(ctor_close_lines))
print('Prototype methods: ' + str(len(proto_lines)))
print('Method closings: ' + str(len(method_close_lines)))
print('Static properties: ' + str(len(static_prop_lines)))
print('Last method close per class: ' + str(last_method_close))

# Verify no prototype patterns remain
remaining_proto = sum(1 for l in output if proto_pattern.match(l))
remaining_ctor = sum(1 for l in output if ctor_pattern.match(l))
print('Remaining prototype patterns: ' + str(remaining_proto))
print('Remaining constructor patterns: ' + str(remaining_ctor))
