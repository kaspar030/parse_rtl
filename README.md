# Overview

Tool to parse gcc's RTL dumps.

## Usage

- compile with CFLAGS+=-fdump-rtl-expand
- check filename of generated .expand files. Current code assumes "*.c.213r.expand". If yours looks different, adjust _rtl_expand_suffix variable in parse_syms.py
- run "parse_syms.py <path-to-expand_files> >out.dot
- create pdf, e.g., "dot -Tpdf out.dot > out.pdf"
