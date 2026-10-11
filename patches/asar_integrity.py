#!/usr/bin/env python3
"""Read-only packed ASAR integrity gate and integrity-aware same-length edits."""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from patch_codex_asar_model_availability_filter import packed_entries, read_header, repack, walk


def repack_same_length_changes(asar: Path, original: bytes, patched: bytes):
    """Preserve archive-wide matchers, but only permit edits inside packed files."""
    if len(original) != len(patched):
        raise RuntimeError("Same-length ASAR patch changed archive length")
    header, payload_start = read_header(asar)
    changes = {}
    cursor = 0
    before, after = memoryview(original), memoryview(patched)
    for path, meta, offset in packed_entries(header):
        start = payload_start + offset
        end = start + int(meta["size"])
        if start < cursor or end > len(original):
            raise RuntimeError(f"Invalid packed ASAR extent: {path}")
        if before[cursor:start] != after[cursor:start]:
            raise RuntimeError("ASAR patch changed header or bytes outside packed files")
        if before[start:end] != after[start:end]:
            changes[path] = bytes(after[start:end])
        cursor = end
    if before[cursor:] != after[cursor:]:
        raise RuntimeError("ASAR patch changed bytes outside packed files")
    if changes:
        repack(asar, header, payload_start, changes)
    return sorted(changes)


def _nonnegative_int(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"invalid {label}")
    result = int(value)
    if result < 0:
        raise ValueError(f"negative {label}")
    return result


def scan_integrity(asar: Path):
    """Stream file and block digests; never output payload content or repair it."""
    result = {"checked": 0, "without_integrity": 0, "unpacked": 0,
              "mismatch_count": 0, "mismatched": []}

    def fail(path, problem):
        result["mismatch_count"] += 1
        if len(result["mismatched"]) < 100:
            result["mismatched"].append(f"{path}: {problem}")

    try:
        with asar.open("rb") as stream:
            length = asar.stat().st_size
            prefix = stream.read(16)
            if len(prefix) != 16:
                raise ValueError("truncated ASAR prefix")
            first, header_size, pickle_size, json_size = struct.unpack("<IIII", prefix)
            if (first != 4 or json_size <= 0 or json_size > 64 * 1024 * 1024
                    or header_size != pickle_size + 4
                    or pickle_size != 4 + json_size + (-json_size % 4)
                    or 8 + header_size > length):
                raise ValueError("invalid ASAR header sizes")
            header = json.loads(stream.read(json_size))
            if not isinstance(header, dict) or not isinstance(header.get("files"), dict):
                raise ValueError("invalid ASAR file tree")
            payload_start = 8 + header_size
            for path, meta in walk(header):
                try:
                    if meta.get("unpacked"):
                        result["unpacked"] += 1
                        continue
                    if "link" in meta:
                        continue
                    offset = _nonnegative_int(meta.get("offset"), "offset")
                    size = _nonnegative_int(meta.get("size"), "size")
                    if payload_start + offset + size > length:
                        raise ValueError("packed extent exceeds archive (truncated payload)")
                    integrity = meta.get("integrity")
                    if integrity is None:
                        result["without_integrity"] += 1
                        continue
                    if not isinstance(integrity, dict) or integrity.get("algorithm") != "SHA256":
                        raise ValueError("invalid/unsupported integrity algorithm")
                    block_size = _nonnegative_int(integrity.get("blockSize"), "blockSize")
                    if block_size == 0:
                        raise ValueError("zero blockSize")
                    blocks = integrity.get("blocks")
                    digest = integrity.get("hash")
                    if (not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
                            or not isinstance(blocks, list)
                            or any(not isinstance(b, str) or not re.fullmatch("[0-9a-f]{64}", b)
                                   for b in blocks)):
                        raise ValueError("invalid integrity digests")
                    result["checked"] += 1
                    count = max(1, (size + block_size - 1) // block_size)
                    problems = []
                    if len(blocks) != count:
                        problems.append(f"block-count ({len(blocks)} vs {count})")
                    file_hash = hashlib.sha256()
                    stream.seek(payload_start + offset)
                    remaining = size
                    for index in range(count):
                        block_hash = hashlib.sha256()
                        in_block = min(block_size, remaining)
                        while in_block:
                            chunk = stream.read(min(in_block, 1024 * 1024))
                            if not chunk:
                                raise ValueError("unexpected EOF in packed payload")
                            file_hash.update(chunk)
                            block_hash.update(chunk)
                            remaining -= len(chunk)
                            in_block -= len(chunk)
                        if index < len(blocks) and block_hash.hexdigest() != blocks[index]:
                            if "block-hash" not in problems:
                                problems.append("block-hash")
                    if file_hash.hexdigest() != digest:
                        problems.append("file-hash")
                    if problems:
                        fail(path, ", ".join(problems))
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    fail(path, str(exc))
    except (OSError, ValueError, TypeError, struct.error, AttributeError) as exc:
        fail("(archive)", str(exc))
    return result


def require_integrity(asar: Path):
    result = scan_integrity(asar)
    print(f"ASAR entry integrity: checked {result['checked']} files; "
          f"mismatched {result['mismatch_count']}; unpacked {result['unpacked']}; "
          f"without integrity {result['without_integrity']}")
    if result["mismatch_count"]:
        for problem in result["mismatched"]:
            print(f"  FAIL: {problem}")
        raise SystemExit(1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar", type=Path)
    args = parser.parse_args()
    require_integrity(args.asar)


if __name__ == "__main__":
    main()
