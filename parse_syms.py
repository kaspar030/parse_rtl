#!/usr/bin/env python
import argparse
import re
import os
import subprocess
import sys

type_memory = { 'd', 'D', 'b', 'B' }
type_flash = { 'd', 'D', 't', 'T', 'r', 'R'}
type_static = { 'd', 't', 'r', 'b' }
type_function = {'t', 'T'}

ignored = { "irq_arch_disable", "irq_arch_restore"}
NM = "arm-none-eabi-nm"
_rtl_expand_suffix = "c.213r.expand"

re_newfile = re.compile(r'^(.*):$')

# "00000000 00000018 R assert_crash_message"
re_symdef = re.compile(r'^(\w{8,}) ([\d]{8,}) (.) ([\w.]+)$')

# "         U sched_context_switch_request"
re_symdep = re.compile(r'^         U (\w+)$')

# ";; Function _callback_unlock_mutex"
re_rtl_func = re.compile(r'^;; Function (\w+) \(.*$')

# ... "(call (mem:SI (symbol_ref:SI ("mutex_unlock")"
re_rtl_func_call = re.compile(r'^.*\(call \(mem:SI \(symbol_ref:SI \(\"(\w+)\"\).*$')

# ..."(mem/f/c:SI (reg/f:SI 120) [1 main_name+0 S4 A32]))"
re_rtl_symbol_ref = re.compile(r'^.*\(mem.+:.I \(reg/f:SI \d+\) \[\d+ (\w+)\+0 S\d+ A\d+\]\)\).*$')

def dprint(*args):
    print(*args, file=sys.stderr)

class Archive(object):
    _map = set()
    def __init__(s, name):
        dprint("new archive: \"%s\"" % name)
        s.name = name
        s.objects = {}
        #s.symbols = {}
        Archive._map.add(s)

    def is_used(s):
        for oname, obj in s.objects.items():
            for sname, symbol in obj.symbols.items():
                if symbol.is_used():
                    return True
        return False

class Obj(object):
    def __init__(s, name, archive):
        dprint("new object:  \"%s\"" % name)
        s.name = name
        s.symbols = {}
        s.archive = archive
        archive.objects[name] = s

class Symbol(object):
    _map = {}
    unmangled_map = {}
    def __init__(s, name, _type, size, obj = None, **kwargs):
        dprint("new symbol:  %s type: %s size=%s" % (name, _type, size), kwargs)
        s.name = name
        s._type = _type
        s.size = size
        s.obj = obj
        s.deps = set()
        s.used_by = set()
        s.stack_usage = -1
        _prefix = kwargs.get('prefix') or ""
        s._global_name = _prefix + name
        s.used = False
        if obj:
            obj.symbols[name] = s
            if _type in type_static:
                s._global_name = Symbol.global_name(name, obj)

        if _prefix or _type in type_static:
            Symbol.unmangled_map[name] = s

        Symbol._map[s._global_name] = s

    def is_used(s):
        if s._type == 'W':
            return False
        if s.used:
            return True

        for used_by in s.used_by:
            if used_by.is_used():
                return True

        return False

    def global_name(name, obj):
        return os.path.basename(obj.archive.name) + ":" + obj.name + ":" + name

    def get_global_name(name, obj):
        if name in Symbol._map or not Symbol.global_name(name, obj) in Symbol._map:
            return name
        else:
            return Symbol.global_name(name, obj)

    def get(name, obj):
        return Symbol._map.get(Symbol.get_global_name(name, obj))

    def get_dep_size(s):
        res = 0
        for dep in s.deps:
            res += dep.size + dep.get_dep_size()
        return res

    def get_stack_usage(s, depth=0):
        print(s.name)
        if s.stack_usage == -1:
            res = 0
            _unknown = 1
        else:
            res = s.stack_usage
            _unknown = 0

        _max = 0
        _depth = depth
        for dep in s.deps:
            if not dep._type in type_function:
                continue
            _bytes, ndepth, unknown = dep.get_stack_usage(depth+1)
            if _bytes > _max or (_bytes == _max and ndepth > _depth):
                _max = _bytes
                _depth = ndepth
            _unknown = _unknown | unknown

        if depth == 0:
            return res + _max + (_depth * 0), _depth, _unknown
        else:
            return res + _max, _depth, _unknown

def parse_syms(archive_files):
    archives = []
    archive = None
    obj = None

    for line in subprocess.check_output("%s -S -t d -p --synthetic %s | grep -v '^00000000 n wm4'" % (NM, archive_files), shell=True).decode().split('\n'):
        if len(line) == 0:
            continue

        m = re_newfile.match(line)
        if m:
            fname = m.group(1)
            if fname.endswith(".a"):
                archive = Archive(fname)
                archives.append(archive)
                obj = None
            else:
                obj = Obj(fname, archive)

            continue

        m = re_symdef.match(line)
        if m:
            name = m.group(4)
            _type = m.group(3)
            size = int(m.group(2))

            Symbol(name, _type, size, obj)
            continue

        m = re_symdep.match(line)
        if m:
            continue

        dprint("Warning: unexpected line:")
        dprint(line)

def parse_elfsyms(fname):
    for line in subprocess.check_output("%s -S -t d -p --synthetic %s | grep -v '^00000000 n wm4'" % (NM, fname), shell=True).decode().split('\n'):
        if len(line) == 0:
            continue

        m = re_symdef.match(line)
        if m:
            name = m.group(4)
            _type = m.group(3)
            size = int(m.group(2))

            symbol = Symbol._map.get(name)
            if symbol:
                    symbol._type = _type
                    symbol.size = size
            if not symbol:
                if _type in { 'b', 't', 'd', 'r' }:
                    symbol = Symbol.unmangled_map.get(name)

            if not symbol:
                symbol = Symbol(name, _type, size, prefix="external:")

            symbol.used = True

def parse_rtl(fname, obj):
    function = None
    dprint("parsing", fname)
    for line in open(fname, "r"):
        if len(line) == 1:
            continue
        line = line.rstrip()

        m = re_rtl_func.match(line)
        if m:
            name = m.group(1)
            function = Symbol._map[Symbol.get_global_name(name, obj)]
            continue

        if not function:
            continue

        m = re_rtl_func_call.match(line) or re_rtl_symbol_ref.match(line)
        if m:
            ref = m.group(1)
            ref_sym = Symbol._map.get(Symbol.get_global_name(ref, obj))
            if not ref_sym:
                ref_sym = Symbol.unmangled_map.get(ref)

            if ref_sym:
                function.deps.add(ref_sym)
                ref_sym.used_by.add(function)
            else:
                dprint("unknown symbol", ref)

def parse_rtl_files():
    dprint("Parsing rtl files...")
    for archive in Archive._map:
        archive_dir = archive.name[:-2]
        for name, obj in archive.objects.items():
            rtl_file = os.path.join(archive_dir, name[:-1] + _rtl_expand_suffix)
            if os.path.isfile(rtl_file):
                parse_rtl(rtl_file, obj)
            else:
                dprint("Warning: no file", rtl_file)

def parse_stack_usage(fname, obj):
    function = None
    dprint("parsing", fname)
    for line in open(fname, "r"):
        if len(line) == 1:
            continue
        line = line.rstrip()

        tmp, nbytes, static = line.split("\t")
        source, line, pos, func = tmp.split(":")
        dprint(obj.name, source, func, nbytes)
        sym = Symbol.get(func, obj)
        if not sym:
            continue

        sym.stack_usage = int(nbytes)

def parse_stackusage_files():
    dprint("Parsing stack usage files...")
    for archive in Archive._map:
        archive_dir = archive.name[:-2]
        for name, obj in archive.objects.items():
            su_file = os.path.join(archive_dir, name[:-1] + "su")
            if os.path.isfile(su_file):
                parse_stack_usage(su_file, obj)
            else:
                dprint("Warning: no file", su_file)

def generate_archive_clusters():
    for archive in Archive._map:
        if not archive.is_used():
            continue

        print (" subgraph \"cluster_%s\" {" % os.path.basename(archive.name))
        print(" color=black")
        print(" label=\"%s\"" % os.path.basename(archive.name))
        for oname, obj in archive.objects.items():
            print (" subgraph \"cluster_%s\" {" % oname)
            print(" color=grey")
            print(" label=\"%s\"" % oname)

            for sname, symbol in obj.symbols.items():
                if symbol.is_used():
                    if not symbol._type in { 'W' }:
                        attrs = {}
                        attrs["label"] = "\\N\\ntype: %s size: %s" % (symbol._type, symbol.size)
                        if symbol._type in type_memory:
                            attrs["peripheries"] = "2"
                        if symbol._type in type_flash:
                            attrs["shape"] = "box"
                        _attrs = []
                        for name, value in attrs.items():
                            _attrs.append("%s=\"%s\"" % (name, value))
                        print ("\"%s\" [%s];" % (symbol.name, ",".join(_attrs)))
            print("}")
        print("}")

def generate_callgraph():
    print("digraph G {")
    print("overlap = false;")

    _external_syms = set()
    for name, symbol in Symbol._map.items():
        if name.startswith("external:"):
            _external_syms.add(symbol)

    if _external_syms:
        _external_archive = Archive("external")
        _external_obj = Obj("external", _external_archive)
        for symbol in _external_syms:
            _external_obj.symbols[symbol.name] = symbol

    generate_archive_clusters()

    for name, symbol in Symbol._map.items():
        if not symbol.is_used():
            continue
        if symbol._type in type_function:
            for dep in symbol.deps:
                if not dep.name in ignored:
                    print("\"%s\" -> \"%s\"" % (symbol.name, dep.name))
    print("}")

def calculate_stack_usage():
    tmp = []
    for name, symbol in Symbol._map.items():
        if not symbol.is_used():
            continue
        if symbol._type in type_function:
            tmp.append((symbol.name, symbol.get_stack_usage()))

    for name, data in sorted(tmp, key=lambda x: x[1][0], reverse=True):
        nbytes = data[0]
        depth = data[1]
        if data[2]: unknown = True
        else: unknown = False
        print("%-30s%6i %2i (calls unknown: %s)" % (name, nbytes, depth, unknown))

def total_sizes():
    text = 0
    data = 0
    bss = 0

    for sname, symbol in Symbol._map.items():
        if not symbol.is_used():
            continue
        print("%08s %s %s" % (symbol.size, symbol._type, symbol.name))
        if symbol._type in type_flash:
            if symbol._type in type_memory:
                data += symbol.size
            else:
                text += symbol.size
        else:
            bss += symbol.size

    print("text:", text, "data:", data, "bss:", bss)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bindir", help="set bindir")
    parser.add_argument("--nm", "-n", help="set name of nm tool")
    args=parser.parse_args()

    if args.nm:
        NM=args.nm

    parse_syms(args.bindir + "/*.a")
    parse_elfsyms(args.bindir + "/*.elf")
    parse_rtl_files()
    parse_stackusage_files()

    generate_callgraph()
    #calculate_stack_usage()
    #total_sizes()
