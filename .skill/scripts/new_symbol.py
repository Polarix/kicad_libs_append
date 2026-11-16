#!/usr/bin/env python3
"""Helper CLI for the new_symbol skill: datasheet PDF/URL -> KiCAD symbol.

Commands
  extract <source>          Extract text / page images from a datasheet PDF, HTML file or URL.
  add <lib> <spec.json>     Generate a symbol from a JSON spec and append it to a .kicad_sym library.
  check <lib>               Structural, duplicate and grid checks for a .kicad_sym library.
  render <lib>              Export a symbol (or the whole library) to SVG through kicad-cli.

Only the Python standard library is required. PDF work uses pymupdf when available
and falls back to pypdf.
"""

from __future__ import annotations

import argparse
import codecs
import glob
import html.parser
import json
import locale
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

GRID = 1.27
PIN_LENGTH = 2.54
PIN_SPACING = 2.54
FONT_SIZE = "1.27 1.27"
PIN_NAME_OFFSET = "1.016"
MIN_HALF_WIDTH = 7.62
MIN_HALF_HEIGHT = 12.7

ELECTRICAL_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "no_connect",
}
TYPE_ALIASES = {
    "power": "power_in", "gnd": "power_in", "ground": "power_in",
    "in": "input", "out": "output", "bidi": "bidirectional", "bidir": "bidirectional",
    "3state": "tri_state", "tristate": "tri_state", "nc": "no_connect",
    "oc": "open_collector", "oe": "open_emitter", "passiv": "passive",
}
SIDES = ("left", "right", "top", "bottom")
GROUND_NAMES = {
    "GND", "VSS", "VEE", "AGND", "DGND", "GNDA", "GNDD", "GNDPWR",
    "EP", "EPAD", "PAD", "0V",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def num(value: float) -> str:
    """Render a coordinate the way KiCAD does (no trailing zeros)."""
    value = round(float(value), 4)
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def q(value: str) -> str:
    """Quote a string for an S-expression."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def read_text(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read()
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    return ("\ufeff" if bom else "") + text


def write_text(path: str, text: str) -> None:
    bom = text.startswith("\ufeff")
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
        handle.write(text)


def strip_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def read_web_text(path: str) -> str:
    """Decode HTML/plain sources, which are often GBK on Chinese vendor sites."""
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def decode_output(data: bytes | None) -> str:
    """kicad-cli prints in the OS locale on Windows and UTF-8 elsewhere."""
    if not data:
        return ""
    candidates = ["utf-8", "mbcs", locale.getpreferredencoding(False), "latin-1"]
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# extract: datasheet acquisition
# --------------------------------------------------------------------------

class _HtmlText(html.parser.HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "td", "th", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.links.append(value)
        elif tag in self.BLOCK_TAGS:
            self.chunks.append("\n")
        elif tag == "img":
            for key, value in attrs:
                if key == "src" and value:
                    self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.chunks.append(data)

    def text(self) -> str:
        joined = "".join(self.chunks)
        joined = re.sub(r"[ \t\u00a0]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*\n+", "\n\n", joined)
        return joined.strip()


def _download(url: str, out_dir: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; kicad-new-symbol/1.0)"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
        disposition = response.headers.get("Content-Disposition", "")

    name = ""
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.I)
    if match:
        name = os.path.basename(urllib.parse.unquote(match.group(1)))
    if not name:
        name = os.path.basename(urllib.parse.urlparse(final_url).path) or "download"

    looks_pdf = "pdf" in content_type.lower() or name.lower().endswith(".pdf")
    if looks_pdf and not name.lower().endswith(".pdf"):
        name += ".pdf"
    if not looks_pdf and not os.path.splitext(name)[1]:
        name += ".html"

    path = os.path.join(out_dir, name)
    with open(path, "wb") as handle:
        handle.write(data)
    print(f"downloaded {url}\n        -> {path} ({content_type or 'unknown type'}, {len(data)} bytes)")
    return path


def _resolve_source(source: str, out_dir: str) -> tuple[str, str]:
    if re.match(r"^https?://", source, re.I):
        path = _download(source, out_dir)
    else:
        path = os.path.abspath(source)
        if not os.path.isfile(path):
            die(f"source not found: {source}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf", path
    if ext in (".html", ".htm"):
        return "html", path
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
        return "image", path
    return "text", path


def _select_pages(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(1, count + 1))
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                first, last = part.split("-", 1)
                selected.update(range(int(first), int(last) + 1))
            else:
                selected.add(int(part))
        except ValueError:
            die(f"invalid --pages value: {part!r} (expected e.g. 1-3,7)")
    return [page for page in sorted(selected) if 1 <= page <= count]


def _open_pdf(path: str):
    try:
        import pymupdf  # type: ignore
        return "pymupdf", pymupdf.open(path)
    except ImportError:
        pass
    try:
        import fitz  # type: ignore
        return "pymupdf", fitz.open(path)
    except ImportError:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        return "pypdf", PdfReader(path)
    except ImportError:
        die("no PDF backend found; install pymupdf (recommended) or pypdf")


def _write_html(source: str, path: str, out_dir: str) -> None:
    parser = _HtmlText()
    parser.feed(read_web_text(path))
    text = parser.text()
    stem = os.path.splitext(os.path.basename(path))[0]
    text_path = os.path.join(out_dir, f"{stem}.txt")
    write_text(text_path, text + "\n")
    print(f"text   -> {text_path} ({len(text)} chars)")
    if parser.links:
        base = source if re.match(r"^https?://", source, re.I) else None
        interesting = []
        for link in parser.links:
            if base:
                link = urllib.parse.urljoin(base, link)
            elif not os.path.isabs(link):
                link = os.path.abspath(os.path.join(os.path.dirname(path), link))
            if re.search(r"\.pdf($|\?)", link, re.I) or "datasheet" in link.lower():
                if link not in interesting:
                    interesting.append(link)
        if interesting:
            print("candidate datasheet links:")
            for link in interesting[:15]:
                print(f"  {link}")


def cmd_extract(args: argparse.Namespace) -> int:
    stem = os.path.splitext(os.path.basename(
        urllib.parse.urlparse(args.source).path.rstrip("/")))[0] or "datasheet"
    out_dir = args.out or os.path.join(tempfile.gettempdir(), "new_symbol", stem)
    os.makedirs(out_dir, exist_ok=True)

    kind, path = _resolve_source(args.source, out_dir)
    if kind == "html":
        _write_html(args.source if re.match(r"^https?://", args.source, re.I) else path, path, out_dir)
        return 0
    if kind == "text":
        text = read_web_text(path)
        out_path = os.path.join(out_dir, os.path.basename(path))
        if os.path.abspath(path) != os.path.abspath(out_path):
            write_text(out_path, text)
        print(f"text   -> {out_path} ({len(text)} chars)")
        return 0
    if kind == "image":
        print(f"image  -> {path}")
        print("hint: this is already an image; open it directly with the view tool.")
        return 0

    backend, doc = _open_pdf(path)
    page_count = doc.page_count if backend == "pymupdf" else len(doc.pages)
    pages = _select_pages(args.pages, page_count)
    if not pages:
        die(f"no pages selected (document has {page_count} pages)")
    combined_parts = [f"# {os.path.basename(path)} ({backend}, {page_count} pages)"]
    extracted = 0
    page_chars: list[tuple[int, int]] = []

    for number in pages:
        if backend == "pymupdf":
            page = doc[number - 1]
            text = page.get_text()
            if args.render:
                pixmap = page.get_pixmap(dpi=args.dpi)
                image_path = os.path.join(out_dir, f"{stem}_p{number:02d}.png")
                pixmap.save(image_path)
                print(f"image  -> {image_path}")
        else:
            text = doc.pages[number - 1].extract_text() or ""
        extracted += len(text.strip())
        page_chars.append((number, len(text.strip())))
        page_path = os.path.join(out_dir, f"{stem}_p{number:02d}.txt")
        write_text(page_path, text if text.endswith("\n") else text + "\n")
        combined_parts.append(f"\n===== page {number} =====\n{text.rstrip()}")

    combined_path = os.path.join(out_dir, f"{stem}.txt")
    write_text(combined_path, "\n".join(combined_parts) + "\n")
    print(f"pages  -> {len(pages)} selected of {page_count}")
    print(f"text   -> {combined_path} ({extracted} non-blank chars)")
    low = [str(number) for number, count in page_chars if count < 50]
    if low:
        print(f"warning: little or no text on page(s) {', '.join(low)}; pin tables there are "
              "probably images. Read the rendered PNGs with the view tool.")
    elif extracted < 200 * len(pages):
        print("warning: very little text extracted; the datasheet is probably scanned. "
              "Re-run with --render and read the page images.")
    if not args.render:
        print("hint: pin tables are often images; add --render to dump page PNGs for visual reading.")
    return 0


# --------------------------------------------------------------------------
# S-expression parsing / structure checks
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse_sexpr(text: str) -> list:
    stack: list[list] = [[]]
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise ValueError(f"unbalanced ')' at offset {match.start()}")
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(token[1:-1] if token.startswith('"') else token)
    if len(stack) != 1:
        raise ValueError(f"{len(stack) - 1} unclosed '('")
    return stack[0]


def _child(node: list, key: str) -> list | None:
    for item in node:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def _children(node: list, key: str) -> list[list]:
    return [item for item in node if isinstance(item, list) and item and item[0] == key]


def _find_root(tree: list) -> list:
    for node in tree:
        if isinstance(node, list) and node and node[0] == "kicad_symbol_lib":
            return node
    raise ValueError("not a kicad_symbol_lib file")


def _library_symbols(tree: list) -> list[list]:
    root = _find_root(tree)
    return [node for node in root if isinstance(node, list) and node and node[0] == "symbol"]


def _library_version(tree: list) -> int | None:
    try:
        version = _child(_find_root(tree), "version")
    except ValueError:
        return None
    if version and len(version) > 1:
        try:
            return int(version[1])
        except (TypeError, ValueError):
            return None
    return None


def _pins_of(symbol: list) -> list[list]:
    pins: list[list] = []
    for unit in _children(symbol, "symbol"):
        pins.extend(_children(unit, "pin"))
    return pins


def _pin_name(pin: list) -> str:
    node = _child(pin, "name")
    return str(node[1]) if node and len(node) > 1 else ""


def _pin_number(pin: list) -> str:
    node = _child(pin, "number")
    return str(node[1]) if node and len(node) > 1 else ""


def _pin_xy(pin: list) -> tuple[float, float] | None:
    node = _child(pin, "at")
    if not node or len(node) < 3:
        return None
    try:
        return float(node[1]), float(node[2])
    except ValueError:
        return None


def _on_grid(value: float) -> bool:
    return abs(value / GRID - round(value / GRID)) < 1e-6


def _check_library(tree: list, only: str | None, verbose: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    symbols = _library_symbols(tree)

    seen: dict[str, int] = {}
    for symbol in symbols:
        name = str(symbol[1]) if len(symbol) > 1 else "<unnamed>"
        seen[name] = seen.get(name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            errors.append(f"duplicate symbol name: {name} x{count}")

    for symbol in symbols:
        name = str(symbol[1]) if len(symbol) > 1 else "<unnamed>"
        if only and name != only:
            continue
        pins = _pins_of(symbol)
        properties = {str(node[1]) for node in _children(symbol, "property") if len(node) > 1}
        for required in ("Reference", "Value", "Footprint", "Datasheet"):
            if required not in properties:
                errors.append(f"{name}: missing property {required}")
        if "Description" not in properties:
            warnings.append(f"{name}: missing property Description")
        if not pins:
            warnings.append(f"{name}: no pins")
        has_outline = bool(_children(symbol, "rectangle")) or any(
            _children(unit, "polyline") or _children(unit, "rectangle")
            for unit in _children(symbol, "symbol")
        )
        if not has_outline:
            warnings.append(f"{name}: no body outline")

        numbers: dict[str, int] = {}
        coords: dict[tuple[float, float], str] = {}
        for pin in pins:
            number = _pin_number(pin)
            if not number:
                errors.append(f"{name}: pin without number ({_pin_name(pin) or 'unnamed'})")
            numbers[number] = numbers.get(number, 0) + 1
            xy = _pin_xy(pin)
            if xy is None:
                errors.append(f"{name}: pin {number or '?'} has no (at x y r)")
                continue
            if xy in coords:
                warnings.append(f"{name}: pins {coords[xy]} and {number} are stacked at "
                                f"{num(xy[0])},{num(xy[1])} (ok if intentional)")
            coords[xy] = number
            if not (_on_grid(xy[0]) and _on_grid(xy[1])):
                errors.append(f"{name}: pin {number} off grid at {num(xy[0])},{num(xy[1])}")
        for number, count in numbers.items():
            if number and count > 1:
                warnings.append(f"{name}: pin number {number} used {count} times")

        if verbose:
            sides = {"left": 0, "right": 0, "top": 0, "bottom": 0}
            for pin in pins:
                at = _child(pin, "at")
                rotation = float(at[3]) if at and len(at) > 3 else 0.0
                sides[{0: "left", 180: "right", 270: "top", 90: "bottom"}.get(int(rotation) % 360, "left")] += 1
            print(f"  {name}: {len(pins)} pins "
                  f"(L{sides['left']} R{sides['right']} T{sides['top']} B{sides['bottom']})")

    return errors, warnings


def cmd_check(args: argparse.Namespace) -> int:
    path = args.library
    if not os.path.isfile(path):
        die(f"library not found: {path}")
    text = read_text(path)
    try:
        tree = parse_sexpr(strip_bom(text))
    except ValueError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1
    version = _library_version(tree)
    symbols = _library_symbols(tree)
    if args.symbol and not any(len(s) > 1 and str(s[1]) == args.symbol for s in symbols):
        die(f"symbol not found in {path}: {args.symbol}")

    print(f"library: {path}")
    print(f"format : ok (balanced parentheses, version {version})")
    print(f"symbols: {len(symbols)}")
    if args.symbol:
        print(f"filter : {args.symbol}")

    errors, warnings = _check_library(tree, args.symbol, verbose=args.all or bool(args.symbol))
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error  : {error}")
    print("result : " + ("FAIL" if errors else "OK"))
    return 1 if errors else 0


# --------------------------------------------------------------------------
# add: generate a symbol and append it to a library
# --------------------------------------------------------------------------

def _visible_length(name: str) -> int:
    cleaned = re.sub(r"~\{([^}]*)\}", r"\1", name)
    parts = [part for part in cleaned.split("/") if part]
    return max((len(part) for part in parts), default=len(cleaned))


def _normalise_type(pin: dict, warnings: list[str]) -> str:
    raw = str(pin.get("type") or "passive").strip().lower().replace("-", "_")
    value = TYPE_ALIASES.get(raw, raw)
    if value not in ELECTRICAL_TYPES:
        warnings.append(f"pin {pin.get('number')}: unknown type {raw!r}, using passive")
        return "passive"
    return value


def _normalise_name(pin: dict) -> str:
    name = str(pin.get("name") or "").strip()
    return name if name else "~"


def _infer_side(pin_type: str, name: str) -> str:
    cleaned = re.sub(r"~\{([^}]*)\}", r"\1", name)
    token = re.split(r"[/\s]", cleaned, maxsplit=1)[0].strip().upper()
    if pin_type in ("power_in", "power_out") and token in GROUND_NAMES:
        return "bottom"
    if pin_type in ("power_in", "power_out"):
        return "top"
    if pin_type == "output":
        return "right"
    if pin_type == "no_connect":
        return "right"
    return "left"


def _prepare_pins(spec: dict, allow_duplicate_numbers: bool) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    raw_pins = spec.get("pins")
    if not isinstance(raw_pins, list) or not raw_pins:
        die("spec must contain a non-empty 'pins' list")

    pins: list[dict] = []
    numbers: dict[str, int] = {}
    for index, raw in enumerate(raw_pins, start=1):
        if not isinstance(raw, dict):
            die(f"pin #{index} is not an object")
        number = str(raw.get("number") if raw.get("number") is not None else "").strip()
        if not number:
            die(f"pin #{index} has no number")
        pin_type = _normalise_type(raw, warnings)
        name = _normalise_name(raw)
        side = str(raw.get("side") or "").strip().lower() or _infer_side(pin_type, name)
        if side not in SIDES:
            die(f"pin {number}: invalid side {side!r} (use left/right/top/bottom)")
        hidden = raw.get("hide")
        if hidden is None:
            hidden = pin_type == "no_connect"
        numbers[number] = numbers.get(number, 0) + 1
        pins.append({
            "number": number,
            "name": name,
            "type": pin_type,
            "side": side,
            "hide": bool(hidden),
        })

    duplicates = sorted(number for number, count in numbers.items() if count > 1)
    if duplicates and not allow_duplicate_numbers:
        die(f"duplicate pin numbers: {', '.join(duplicates)} (use --allow-duplicate-numbers to override)")
    return pins, warnings


def _layout(pins: list[dict], spec: dict) -> dict:
    by_side = {side: [] for side in SIDES}
    for pin in pins:
        by_side[pin["side"]].append(pin)

    pin_length = float(spec.get("pin_length") or PIN_LENGTH)
    name_length = max((_visible_length(pin["name"]) for pin in pins), default=1)
    top_bottom = max(len(by_side["top"]), len(by_side["bottom"]), 1)
    left_right = max(len(by_side["left"]), len(by_side["right"]), 1)

    min_half_w = max(
        MIN_HALF_WIDTH,
        2.54 + 0.85 * name_length,
        2.54 + (top_bottom - 1) / 2 * PIN_SPACING + GRID,
    )
    min_half_h = max(MIN_HALF_HEIGHT, (left_right - 1) / 2 * PIN_SPACING + PIN_SPACING + GRID)

    body = spec.get("body") or {}
    half_w = max(min_half_w, float(body.get("width") or 0) / 2)
    half_h = max(min_half_h, float(body.get("height") or 0) / 2)
    half_w = math.ceil(half_w / GRID - 1e-9) * GRID
    half_h = math.ceil(half_h / GRID - 1e-9) * GRID

    placed: list[dict] = []
    for side, side_pins in by_side.items():
        count = len(side_pins)
        for index, pin in enumerate(side_pins):
            offset = (count - 1) / 2 * PIN_SPACING - index * PIN_SPACING
            if side == "left":
                x, y, rotation = -(half_w + pin_length), offset, 0
            elif side == "right":
                x, y, rotation = half_w + pin_length, offset, 180
            elif side == "top":
                x, y, rotation = -offset, half_h + pin_length, 270
            else:
                x, y, rotation = offset, -(half_h + pin_length), 90
            placed.append({**pin, "x": x, "y": y, "rotation": rotation})

    placed.sort(key=lambda item: (SIDES.index(item["side"]), item["number"]))
    return {
        "pins": placed,
        "half_width": half_w,
        "half_height": half_h,
        "pin_length": pin_length,
    }


def _property(key: str, value: str, at: tuple[float, float], style: dict,
              justify: str | None = None, hidden: bool = False) -> list[str]:
    lines = [
        f'\t\t(property "{q(key)}" "{q(value)}"',
        f"\t\t\t(at {num(at[0])} {num(at[1])} 0)",
    ]
    if style["v10"]:
        lines += ["\t\t\t(show_name no)", "\t\t\t(do_not_autoplace no)"]
        if hidden:
            lines.append("\t\t\t(hide yes)")
        lines += ["\t\t\t(effects", "\t\t\t\t(font", f"\t\t\t\t\t(size {FONT_SIZE})", "\t\t\t\t)"]
        if justify:
            lines.append(f"\t\t\t\t(justify {justify})")
        lines.append("\t\t\t)")
    else:
        lines += ["\t\t\t(effects", "\t\t\t\t(font", f"\t\t\t\t\t(size {FONT_SIZE})", "\t\t\t\t)"]
        if justify:
            lines.append(f"\t\t\t\t(justify {justify})")
        if hidden:
            lines.append("\t\t\t\t(hide yes)")
        lines.append("\t\t\t)")
    lines.append("\t\t)")
    return lines


def _format_keywords(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def build_symbol(spec: dict, style: dict) -> tuple[str, dict]:
    name = str(spec.get("name") or "").strip()
    if not name:
        die("spec.name is required")
    warnings: list[str] = []
    pins, pin_warnings = _prepare_pins(spec, bool(spec.get("allow_duplicate_numbers")))
    warnings.extend(pin_warnings)

    layout = _layout(pins, spec)
    half_w = layout["half_width"]
    half_h = layout["half_height"]
    pin_length = layout["pin_length"]

    reference = str(spec.get("reference") or "U")
    value = str(spec.get("value") or name)
    footprint = str(spec.get("footprint") or "")
    datasheet = str(spec.get("datasheet") or "")
    description = str(spec.get("description") or "")
    keywords = _format_keywords(spec.get("keywords"))
    fp_filters = _format_keywords(spec.get("fp_filters"))

    lines: list[str] = [f'\t(symbol "{q(name)}"']
    lines += ["\t\t(pin_names", f"\t\t\t(offset {PIN_NAME_OFFSET})", "\t\t)"]
    lines += ["\t\t(exclude_from_sim no)", "\t\t(in_bom yes)", "\t\t(on_board yes)"]
    if style["v10"]:
        lines += ["\t\t(in_pos_files yes)", "\t\t(duplicate_pin_numbers_are_jumpers no)"]

    lines += _property("Reference", reference, (-half_w, half_h + GRID), style, justify="right")
    lines += _property("Value", value, (half_w, half_h + GRID), style, justify="left")
    lines += _property("Footprint", footprint, (0, -(half_h + PIN_SPACING)), style, hidden=True)
    lines += _property("Datasheet", datasheet, (0, 0), style, hidden=True)
    lines += _property("Description", description, (0, 0), style, hidden=True)
    lines += _property("ki_keywords", keywords, (0, 0), style, hidden=True)
    if fp_filters:
        lines += _property("ki_fp_filters", fp_filters, (0, 0), style, hidden=True)

    lines += [
        f'\t\t(symbol "{q(name)}_0_1"',
        "\t\t\t(rectangle",
        f"\t\t\t\t(start {num(-half_w)} {num(half_h)})",
        f"\t\t\t\t(end {num(half_w)} {num(-half_h)})",
        "\t\t\t\t(stroke",
        "\t\t\t\t\t(width 0.254)",
        "\t\t\t\t\t(type default)",
        "\t\t\t\t)",
        "\t\t\t\t(fill",
        "\t\t\t\t\t(type background)",
        "\t\t\t\t)",
        "\t\t\t)",
        "\t\t)",
        f'\t\t(symbol "{q(name)}_1_1"',
    ]

    for pin in layout["pins"]:
        lines += [
            f'\t\t\t(pin {pin["type"]} line',
            f'\t\t\t\t(at {num(pin["x"])} {num(pin["y"])} {pin["rotation"]})',
            f"\t\t\t\t(length {num(pin_length)})",
        ]
        if pin["hide"]:
            lines.append("\t\t\t\t(hide yes)")
        lines += [
            f'\t\t\t\t(name "{q(pin["name"])}"',
            "\t\t\t\t\t(effects",
            "\t\t\t\t\t\t(font",
            f"\t\t\t\t\t\t\t(size {FONT_SIZE})",
            "\t\t\t\t\t\t)",
            "\t\t\t\t\t)",
            "\t\t\t\t)",
            f'\t\t\t\t(number "{q(pin["number"])}"',
            "\t\t\t\t\t(effects",
            "\t\t\t\t\t\t(font",
            f"\t\t\t\t\t\t\t(size {FONT_SIZE})",
            "\t\t\t\t\t\t)",
            "\t\t\t\t\t)",
            "\t\t\t\t)",
            "\t\t\t)",
        ]

    lines += ["\t\t)", "\t\t(embedded_fonts no)", "\t)"]
    block = "\n".join(lines)
    summary = {
        "symbol": name,
        "pins": len(layout["pins"]),
        "body": [round(half_w * 2, 3), round(half_h * 2, 3)],
        "sides": {side: sum(1 for pin in layout["pins"] if pin["side"] == side) for side in SIDES},
        "warnings": warnings,
    }
    return block, summary


def _symbol_span(text: str, name: str) -> tuple[int, int] | None:
    match = re.search(r'^\t\(symbol "' + re.escape(name) + r'"', text, re.M)
    if not match:
        return None
    start = match.start()
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return start, index + 1
    raise ValueError(f"unterminated symbol block: {name}")


def _insert_symbol(text: str, block: str, name: str, replace: bool) -> str:
    newline = newline_of(text)
    block_text = block.replace("\n", newline)
    span = _symbol_span(text, name)
    if span:
        if not replace:
            die(f"symbol already exists in library: {name} (use --replace to overwrite)")
        return text[:span[0]] + block_text + text[span[1]:]

    core = text.rstrip()
    if not core.endswith(")"):
        die("library does not end with the expected closing parenthesis")
    prefix = core[:-1]
    if not prefix.endswith(newline):
        prefix += newline
    return prefix + block_text + newline + ")" + newline


def _style_for_version(version: int | None, path: str) -> dict:
    if version is None:
        die(f"cannot read (version ...) from {path}")
    if version >= 20251024:
        return {"v10": True, "version": version}
    if version >= 20241209:
        return {"v10": False, "version": version}
    die(f"library version {version} is too old for this generator; "
        f"upgrade it first with 'kicad-cli sym upgrade' and commit that change separately")


def cmd_add(args: argparse.Namespace) -> int:
    lib_path = args.library
    if not os.path.isfile(lib_path):
        die(f"library not found: {lib_path}")
    if not os.path.isfile(args.spec):
        die(f"spec not found: {args.spec}")

    with open(args.spec, encoding="utf-8-sig") as handle:
        spec = json.load(handle)

    text = read_text(lib_path)
    try:
        tree = parse_sexpr(strip_bom(text))
    except ValueError as exc:
        die(f"{lib_path}: {exc}")
    style = _style_for_version(_library_version(tree), lib_path)

    block, summary = build_symbol(spec, style)
    name = summary["symbol"]

    if args.dry_run:
        print(block)
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        return 0

    updated = _insert_symbol(text, block, name, args.replace)

    try:
        new_tree = parse_sexpr(strip_bom(updated))
    except ValueError as exc:
        die(f"generated output is not parseable: {exc}")

    errors, warnings = _check_library(new_tree, name, verbose=False)
    hard_errors = [error for error in errors if "duplicate symbol name" not in error]
    if hard_errors:
        for error in hard_errors:
            print(f"error  : {error}", file=sys.stderr)
        die("generated symbol failed self-check; library left untouched")
    for warning in warnings + summary["warnings"]:
        print(f"warning: {warning}")

    if not args.no_backup:
        backup = lib_path + ".bak"
        shutil.copyfile(lib_path, backup)
        print(f"backup : {backup}")

    write_text(lib_path, updated)
    print(f"library: {lib_path} (version {style['version']}, style {'v10' if style['v10'] else 'v9'})")
    print(f"symbol : {name}, {summary['pins']} pins, body {num(summary['body'][0])}x{num(summary['body'][1])} mm")
    print("edges  : " + ", ".join(f"{side}={count}" for side, count in summary["sides"].items() if count))
    print(f"verify : python .skill/scripts/new_symbol.py check {lib_path} --symbol {name}")
    return 0


# --------------------------------------------------------------------------
# render: kicad-cli SVG export
# --------------------------------------------------------------------------

def find_kicad_cli() -> str:
    found = shutil.which("kicad-cli")
    if found:
        return found
    patterns = [
        r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
        r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    die("kicad-cli not found; install KiCAD or add kicad-cli to PATH")


def cmd_render(args: argparse.Namespace) -> int:
    lib_path = os.path.abspath(args.library)
    if not os.path.isfile(lib_path):
        die(f"library not found: {lib_path}")
    out_dir = os.path.abspath(args.out or os.path.join(tempfile.gettempdir(), "new_symbol", "svg"))
    os.makedirs(out_dir, exist_ok=True)

    command = [find_kicad_cli(), "sym", "export", "svg", "-o", out_dir]
    if args.symbol:
        command += ["--symbol", args.symbol]
    if args.include_hidden_pins:
        command.append("--include-hidden-pins")
    command.append(lib_path)
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        message = decode_output(result.stderr) or decode_output(result.stdout)
        if message.strip():
            print(message.strip(), file=sys.stderr)
        return result.returncode

    produced = sorted(glob.glob(os.path.join(out_dir, "*.svg")))
    print(f"svg    : {out_dir} ({len(produced)} file(s))")
    for path in produced[:20]:
        print(f"  {path}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Datasheet PDF/URL -> KiCAD symbol helper")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="extract text and page images from a datasheet")
    extract.add_argument("source", help="PDF path, HTML path or http(s) URL")
    extract.add_argument("--out", help="output directory (default: system temp)")
    extract.add_argument("--pages", help="page selection such as 1-3,7 (default: all)")
    extract.add_argument("--render", action="store_true", help="also write page PNGs")
    extract.add_argument("--dpi", type=int, default=200, help="PNG resolution (default 200)")
    extract.set_defaults(func=cmd_extract)

    add = sub.add_parser("add", help="generate a symbol from a JSON spec and append it")
    add.add_argument("library", help="target .kicad_sym library")
    add.add_argument("spec", help="JSON symbol specification")
    add.add_argument("--dry-run", action="store_true", help="print the generated block only")
    add.add_argument("--replace", action="store_true", help="replace an existing symbol of the same name")
    add.add_argument("--no-backup", action="store_true", help="do not write <library>.bak")
    add.set_defaults(func=cmd_add)

    check = sub.add_parser("check", help="check a symbol library")
    check.add_argument("library")
    check.add_argument("--symbol", help="only check this symbol")
    check.add_argument("--all", action="store_true", help="print per-symbol pin counts")
    check.set_defaults(func=cmd_check)

    render = sub.add_parser("render", help="export symbols to SVG with kicad-cli")
    render.add_argument("library")
    render.add_argument("--symbol", help="only export this symbol")
    render.add_argument("--out", help="output directory (default: system temp)")
    render.add_argument("--include-hidden-pins", action="store_true",
                        help="also draw hidden pins (useful to verify NC pins)")
    render.set_defaults(func=cmd_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
