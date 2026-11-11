"""Read component metadata from Android's compiled manifest (ResXMLTree)."""

import struct
from functools import lru_cache
from xml.etree.ElementTree import Element

ANDROID = "{http://schemas.android.com/apk/res/android}"
MAX_MANIFEST = 4 * 1024 * 1024
NONE = 0xFFFFFFFF


def _string_reader(chunk, header):
    count, _, flags, start, _ = struct.unpack_from("<5I", chunk, 8)
    if header < 28 or header + 4 * count > start or start > len(chunk):
        raise ValueError("Invalid Android string pool")
    utf8 = bool(flags & 0x100)

    def length(pos, unit):
        mask = 0x80 if unit == 1 else 0x8000
        fmt = "<B" if unit == 1 else "<H"
        first = struct.unpack_from(fmt, chunk, pos)[0]
        if first & mask:
            second = struct.unpack_from(fmt, chunk, pos + unit)[0]
            return ((first & (mask - 1)) << (unit * 8)) | second, pos + unit * 2
        return first, pos + unit

    @lru_cache(maxsize=4096)
    def read(index):
        if index >= count:
            raise ValueError("Invalid Android string index")
        pos = start + struct.unpack_from("<I", chunk, header + 4 * index)[0]
        if utf8:
            _, pos = length(pos, 1)
        unit = 1 if utf8 else 2
        size, pos = length(pos, unit)
        end = pos + size * unit
        if end + unit > len(chunk):
            raise ValueError("Truncated Android string")
        return bytes(chunk[pos:end]).decode("utf-8" if utf8 else "utf-16-le")

    return read


def decode(raw):
    """Decode just XML element names and attributes, preserving namespaces."""
    kind, offset, total = struct.unpack_from("<HHI", raw)
    if kind != 3 or offset != 8 or total != len(raw) or total > MAX_MANIFEST:
        raise ValueError("Invalid Android manifest")
    data = memoryview(raw)
    stack = []
    root = None
    string = None
    while offset < total:
        kind, header, size = struct.unpack_from("<HHI", data, offset)
        if not 8 <= header <= size <= total - offset:
            raise ValueError("Invalid Android manifest chunk")
        chunk = data[offset : offset + size]
        offset += size
        if kind == 1:
            string = _string_reader(chunk, header)
        elif kind in (0x102, 0x103):
            if string is None:
                raise ValueError("Missing Android string pool")
            ns, name = struct.unpack_from("<II", chunk, header)
            tag = ("" if ns == NONE else "{" + string(ns) + "}") + string(name)
            if kind == 0x103:
                if not stack or stack.pop().tag != tag:
                    raise ValueError("Unbalanced manifest")
                continue
            first, stride, count = struct.unpack_from("<3H", chunk, header + 8)
            if first < 20 or stride < 20 or header + first + count * stride > size:
                raise ValueError("Invalid Android attributes")
            elem = Element(tag)
            for index in range(count):
                pos = header + first + stride * index
                ns, key, raw_str, _, _, typ, val = struct.unpack_from(
                    "<IIIHBBI", chunk, pos
                )
                attr = ("" if ns == NONE else "{" + string(ns) + "}") + string(key)
                if typ == 3:
                    value = string(val)
                elif typ == 1:
                    value = "@" + hex(val)
                elif typ == 0x12:
                    value = "true" if val else "false"
                elif raw_str != NONE:
                    value = string(raw_str)
                else:
                    value = str(val)
                elem.set(attr, value)
            if stack:
                stack[-1].append(elem)
            elif root is None:
                root = elem
            else:
                raise ValueError("Multiple manifest roots")
            stack.append(elem)
    if stack or root is None or root.tag != "manifest":
        raise ValueError("Incomplete Android manifest")
    return root
