#!/usr/bin/env python3
"""
Fusionne plusieurs playlists M3U et plusieurs guides XMLTV en supprimant les doublons.
Aucune dépendance externe (Python 3.8+).

Usage :
    python3 fusion_iptv.py
Résultat dans le dossier ./sortie :
    merged.m3u
    merged.xml.gz
    doublons_supprimes.txt   (liste de ce qui a été retiré)
"""
import gzip
import io
import os
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET

# L'ordre = la priorité. En cas de doublon, la PREMIÈRE source gagne.
M3U_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/fr.m3u",
    "https://www.apsattv.com/frlg.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/plu"
    "to/refs/heads/main/pluto_fr.m3u",
]

XMLTV_SOURCES = [
    "https://xmltvfr.fr/xmltv/xmltv_fr.xml.gz",
    "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/all.xml.gz",
]

OUT_DIR = "sortie"
# True = ne garde dans le guide que les chaînes présentes dans le M3U final
FILTER_XMLTV_ON_M3U = True

UA = {"User-Agent": "Mozilla/5.0"}


def download(url: str) -> bytes:
    print(f"Téléchargement : {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":  # gzip
        data = gzip.decompress(data)
    return data


# ---------------------------------------------------------------- M3U
def normalize_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    n = re.sub(r"[\[\(].*?[\]\)]", " ", n)           # [Geo-blocked], (1080p)...
    n = re.sub(r"\b(hd|fhd|uhd|sd|4k|hevc|fr|france|tv)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def parse_m3u(text: str):
    """Retourne une liste de (extinf, [lignes_options], url)."""
    entries, extinf, opts = [], None, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            extinf, opts = line, []
        elif line.startswith("#"):
            opts.append(line)
        elif extinf:
            entries.append((extinf, opts, line))
            extinf, opts = None, []
    return entries


def attr(extinf: str, key: str) -> str:
    m = re.search(rf'{key}="([^"]*)"', extinf)
    return m.group(1) if m else ""


def channel_name(extinf: str) -> str:
    return extinf.rsplit(",", 1)[-1].strip()


def merge_m3u():
    seen_names, seen_urls = {}, set()
    kept, removed = [], []
    for src in M3U_SOURCES:
        try:
            text = download(src).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  !! échec : {e}")
            continue
        for extinf, opts, url in parse_m3u(text):
            name = channel_name(extinf)
            key = normalize_name(name) or url
            if url in seen_urls or key in seen_names:
                removed.append(f"{name}  ({src})  ~ doublon de : {seen_names.get(key, url)}")
                continue
            seen_urls.add(url)
            seen_names[key] = f"{name} ({src})"
            kept.append((extinf, opts, url))
    return kept, removed


# -------------------------------------------------------------- XMLTV
def merge_xmltv(allowed_ids):
    root = ET.Element("tv", {"generator-info-name": "fusion_iptv"})
    seen_channels, seen_progs = set(), set()
    n_ch = n_pr = n_dup = 0
    for src in XMLTV_SOURCES:
        try:
            data = download(src)
        except Exception as e:
            print(f"  !! échec : {e}")
            continue
        for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
            if el.tag == "channel":
                cid = el.get("id")
                if cid in seen_channels or (allowed_ids and cid not in allowed_ids):
                    n_dup += 1
                else:
                    seen_channels.add(cid)
                    root.append(el)
                    n_ch += 1
                    continue
                el.clear()
            elif el.tag == "programme":
                cid = el.get("channel")
                key = (cid, el.get("start"))
                if (allowed_ids and cid not in allowed_ids) or key in seen_progs:
                    n_dup += 1
                    el.clear()
                    continue
                seen_progs.add(key)
                root.append(el)
                n_pr += 1
    print(f"XMLTV : {n_ch} chaînes, {n_pr} programmes, {n_dup} éléments ignorés")
    return root


# --------------------------------------------------------------- main
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    kept, removed = merge_m3u()
    with open(os.path.join(OUT_DIR, "merged.m3u"), "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for extinf, opts, url in kept:
            f.write(extinf + "\n")
            for o in opts:
                f.write(o + "\n")
            f.write(url + "\n")
    with open(os.path.join(OUT_DIR, "doublons_supprimes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(removed))
    print(f"M3U : {len(kept)} chaînes gardées, {len(removed)} doublons supprimés")

    ids = {attr(e, "tvg-id") for e, _, _ in kept} - {""}
    root = merge_xmltv(ids if FILTER_XMLTV_ON_M3U else None)
    # XMLTV exige que les <channel> soient avant les <programme>
    children = list(root)
    root[:] = [c for c in children if c.tag == "channel"] + [c for c in children if c.tag == "programme"]
    ET.indent(root)
    with gzip.open(os.path.join(OUT_DIR, "merged.xml.gz"), "wb") as f:
        ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=True)

    print(f"\nTerminé -> dossier '{OUT_DIR}'")


if __name__ == "__main__":
    main()