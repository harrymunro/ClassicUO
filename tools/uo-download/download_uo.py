#!/usr/bin/env python3
"""Download the official Ultima Online Classic Client data files without Windows.

The uo.com installer is a small Windows bootstrapper whose patcher pulls the real
files from EA's public patch servers. This script speaks the same protocol:

  product manifest (.prod, XML)
    -> package manifest  manifest/<pkg>/pkg.mft            (zlib XML)
      -> unpacked.mft    plain files, fetched from files/<pkg>/unpacked/<hash>
      -> <name>.uop.mft  UOP archives, rebuilt from per-entry chunks
                         fetched from files/<pkg>/<pack>/<ph><sh>

Hashes are the UOP filename hash (Bob Jenkins' lookup3 variant used by the client),
written as "%08x%08x" % (low32, high32). Rebuilt UOPs store every entry uncompressed
with no per-entry metadata header, which ClassicUO and ModernUO both read.

Usage: python3 download_uo.py --out ~/Workspace/UOClassic
Re-running skips files that already exist with the expected size.
"""

import argparse
import http.client
import os
import struct
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

PRODUCT_URL = "http://patch.uo.eamythic.com/uopatch-sa/legacyrelease/uo/manifest/uo-legacyrelease.prod"
PACKAGE = "base"
M32 = 0xFFFFFFFF


def uop_hash(s: str) -> int:
    """Port of ClassicUO.IO.UOFileUop.CreateHash."""
    b = s.encode("latin-1")
    n = len(b)
    eax = ecx = edx = 0
    ebx = edi = esi = (n + 0xDEADBEEF) & M32
    i = 0
    while i + 12 < n:
        edi = (int.from_bytes(b[i + 4:i + 8], "little") + edi) & M32
        esi = (int.from_bytes(b[i + 8:i + 12], "little") + esi) & M32
        edx = (int.from_bytes(b[i:i + 4], "little") - esi) & M32
        edx = ((edx + ebx) & M32) ^ (esi >> 28) ^ ((esi << 4) & M32)
        esi = (esi + edi) & M32
        edi = ((edi - edx) & M32) ^ (edx >> 26) ^ ((edx << 6) & M32)
        edx = (edx + esi) & M32
        esi = ((esi - edi) & M32) ^ (edi >> 24) ^ ((edi << 8) & M32)
        edi = (edi + edx) & M32
        ebx = ((edx - esi) & M32) ^ (esi >> 16) ^ ((esi << 16) & M32)
        esi = (esi + edi) & M32
        edi = ((edi - ebx) & M32) ^ (ebx >> 13) ^ ((ebx << 19) & M32)
        ebx = (ebx + esi) & M32
        esi = ((esi - edi) & M32) ^ (edi >> 28) ^ ((edi << 4) & M32)
        edi = (edi + ebx) & M32
        i += 12
    r = n - i
    if r <= 0:
        return (esi << 32) | eax
    tail = b[i:]
    if r > 8:
        esi = (esi + int.from_bytes(tail[8:r], "little")) & M32
    if r > 4:
        edi = (edi + int.from_bytes(tail[4:min(8, r)], "little")) & M32
    ebx = (ebx + int.from_bytes(tail[0:min(4, r)], "little")) & M32
    esi = ((esi ^ edi) - ((edi >> 18) ^ ((edi << 14) & M32))) & M32
    ecx = ((esi ^ ebx) - ((esi >> 21) ^ ((esi << 11) & M32))) & M32
    edi = ((edi ^ ecx) - ((ecx >> 7) ^ ((ecx << 25) & M32))) & M32
    esi = ((esi ^ edi) - ((edi >> 16) ^ ((edi << 16) & M32))) & M32
    edx = ((esi ^ ecx) - ((esi >> 28) ^ ((esi << 4) & M32))) & M32
    edi = ((edi ^ edx) - ((edx >> 18) ^ ((edx << 14) & M32))) & M32
    eax = ((esi ^ edi) - ((edi >> 8) ^ ((edi << 24) & M32))) & M32
    return (edi << 32) | eax


def hash_path(lo: int, hi: int) -> str:
    return "%08x%08x" % (lo, hi)


class Http:
    """One persistent connection per worker thread, with retries."""

    def __init__(self):
        self._local = threading.local()

    def _conn(self, host):
        conns = getattr(self._local, "conns", None)
        if conns is None:
            conns = self._local.conns = {}
        c = conns.get(host)
        if c is None:
            c = conns[host] = http.client.HTTPConnection(host, timeout=60)
        return c

    def _drop(self, host):
        c = self._local.conns.pop(host, None)
        if c:
            c.close()

    def open(self, url, attempts=6):
        """Return an open response for url; caller must read it fully."""
        u = urllib.parse.urlsplit(url)
        for attempt in range(attempts):
            try:
                c = self._conn(u.netloc)
                c.request("GET", u.path, headers={"Connection": "keep-alive"})
                r = c.getresponse()
                if r.status == 200:
                    return r
                r.read()
                if r.status == 404:
                    raise FileNotFoundError(url)
                raise IOError(f"HTTP {r.status} for {url}")
            except FileNotFoundError:
                raise
            except Exception as e:
                self._drop(u.netloc)
                if attempt == attempts - 1:
                    raise IOError(f"failed after {attempts} attempts: {url}: {e}") from e
                time.sleep(min(2 ** attempt, 20))

    def get(self, url):
        return self.open(url).read()


def mft(http, url):
    return ET.fromstring(zlib.decompress(http.get(url)))


def hexint(v):
    return int(v, 16) if v else 0


def fetch_unpacked(http, files_base, out_dir, f):
    """Download one plain file, streaming through zlib when compressed."""
    name = f.get("n")
    size = hexint(f.get("ul"))
    dest = os.path.join(out_dir, *name.split("/"))
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return name, size, True

    h = uop_hash(name.lower())
    url = f"{files_base}unpacked/{hash_path(h & M32, h >> 32)}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"

    for attempt in range(3):
        r = http.open(url)
        d = zlib.decompressobj() if f.get("ct") == "1" else None
        written = 0
        with open(tmp, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                if d:
                    chunk = d.decompress(chunk)
                fh.write(chunk)
                written += len(chunk)
            if d:
                rest = d.flush()
                fh.write(rest)
                written += len(rest)
        if written == size:
            os.replace(tmp, dest)
            return name, size, False
    raise IOError(f"{name}: expected {size} bytes, got {written}")


def fetch_chunk(http, url, ct, size):
    data = http.get(url)
    if ct == "1":
        data = zlib.decompress(data)
    if len(data) != size:
        raise IOError(f"{url}: expected {size} bytes, got {len(data)}")
    return data


def write_uop(dest, entries):
    """entries: list of (hash64, bytes). Data first, then chained tables."""
    block_size = 1000
    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        fh.write(b"\0" * 0x200)  # header, patched below
        offsets = []
        for _, data in entries:
            offsets.append(fh.tell())
            fh.write(data)

        blocks = [list(range(i, min(i + block_size, len(entries)))) for i in range(0, len(entries), block_size)]
        first_table = fh.tell()
        table_size = 4 + 8 + block_size * 34
        for bi, block in enumerate(blocks):
            next_block = first_table + (bi + 1) * table_size if bi + 1 < len(blocks) else 0
            table = bytearray(table_size)
            struct.pack_into("<iq", table, 0, len(block), next_block)
            for j, idx in enumerate(block):
                h, data = entries[idx]
                struct.pack_into("<qiiiQIh", table, 12 + j * 34,
                                 offsets[idx], 0, len(data), len(data), h, zlib.adler32(data), 0)
            fh.write(table)

        fh.seek(0)
        fh.write(struct.pack("<IIIqIi", 0x50594D, 5, 0xFD23EC43, first_table if blocks else 0, block_size, len(entries)))
    os.replace(tmp, dest)


def collect_pack_entries(http, manifest_base, x):
    """A pack manifest either lists entries or splits them across sub-manifests."""
    packs = {}
    subs = [m.get("n") for m in x.iter("manifest") if m.get("n")]
    for p in x.iter("p"):
        packs.setdefault((p.get("name"), p.get("rpath")), []).extend(p.iter("f"))
    for n in subs:
        for key, fs in collect_pack_entries(http, manifest_base, mft(http, manifest_base + n)).items():
            packs.setdefault(key, []).extend(fs)
    return packs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="destination directory")
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()
    out_dir = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(out_dir, exist_ok=True)
    http = Http()

    prod = ET.fromstring(http.get(PRODUCT_URL))
    manifest_repo = prod.find(".//manifestrepos/repo").get("url")
    files_repo = prod.find(".//filerepos/repo").get("url")
    print(f"product serial {prod.find('product').get('serial')}, files from {files_repo}")

    manifest_base = f"{manifest_repo}{PACKAGE}/"
    files_base = f"{files_repo}{PACKAGE}/"
    pkg = mft(http, manifest_base + "pkg.mft")
    names = [m.get("n") for m in pkg.iter("manifest") if m.get("n")]

    plain = []
    packs = {}
    for n in names:
        x = mft(http, manifest_base + n)
        if n == "unpacked.mft":
            plain = list(x.find("manifest/files"))
        else:
            for key, fs in collect_pack_entries(http, manifest_base, x).items():
                packs.setdefault(key, []).extend(fs)

    total = sum(hexint(f.get("cl") or f.get("ul")) for f in plain)
    print(f"{len(plain)} files ({total / 1e6:.0f} MB compressed), {len(packs)} UOP packs")

    with ThreadPoolExecutor(args.workers) as pool:
        done_bytes = 0
        futures = [pool.submit(fetch_unpacked, http, files_base, out_dir, f) for f in plain]
        for i, fut in enumerate(as_completed(futures), 1):
            name, size, skipped = fut.result()
            done_bytes += size
            if not skipped or i == len(futures):
                print(f"[{i}/{len(futures)}] {'skip' if skipped else 'got '} {name}")

        for (pack_name, rpath), fs in packs.items():
            dest = os.path.join(out_dir, pack_name)
            if os.path.exists(dest):
                print(f"skip {pack_name} (exists)")
                continue
            print(f"building {pack_name} from {len(fs)} entries...")
            chunks = {}
            futs = {
                pool.submit(fetch_chunk, http, f"{files_base}{rpath}/{f.get('ph').rjust(8, '0')}{f.get('sh').rjust(8, '0')}",
                            f.get("ct"), hexint(f.get("ul"))): f
                for f in fs
            }
            for fut in as_completed(futs):
                f = futs[fut]
                chunks[(hexint(f.get("sh")) << 32) | hexint(f.get("ph"))] = fut.result()
            write_uop(dest, sorted(chunks.items()))
            print(f"wrote {pack_name} ({os.path.getsize(dest) / 1e6:.1f} MB)")

    print("done:", out_dir)


if __name__ == "__main__":
    sys.exit(main())
