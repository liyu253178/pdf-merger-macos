"""PDF 去水印：对象级检测与内容流删除。

设计原则
--------
去水印不是「把水印盖住」，而是把生成水印的那个内容流对象删掉：

* 文字水印：定位包含水印文字的 ``q … Q`` 内容块，只删这个块；
* 图片水印：定位绘制小图（非整页扫描底图）的 ``Do`` 块；
* 注释水印：直接删除对应的注释对象。

不做整页栅格化，也不使用 Redaction。Redaction 会连带删掉水印下方
重叠的正文（实测一份样本会损失 70.8% 的正文字符），因此这里实现
的是对象级删除。

实现边界
--------
PDF 内容流没有「对象」这种高层结构，只有 ``q/Q`` 保存/恢复图形状态。
本模块把每个 ``q … Q`` 当成一个可删除单元，并用以下信息判断它是否
安全：

1. 块内文字经 ToUnicode CMap 解码后是否命中查询；
2. 块内是否只有同一字体/字号/透明度的一组文字（水印通常如此）；
3. 块内是否混入图片或其它正文文字。

无法确定安全时不会删除，而是把对象标记为不可移除，交给界面提示。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

import pymupdf


# --------------------------------------------------------------------------- #
#  数据模型
# --------------------------------------------------------------------------- #

@dataclass
class WatermarkObject:
    """一个可展示、可勾选的水印对象。"""

    oid: str
    page: int                         # 0 基页码
    kind: str                         # text / image / annot
    bbox: tuple[float, float, float, float]
    text: str = ""
    font: str = ""
    size: float = 0.0
    color: tuple[float, float, float] | None = None
    opacity: float = 1.0
    confidence: str = "high"          # high / medium / low
    removable: bool = True
    note: str = ""
    doc_path: str = ""                # 所属 PDF（多文件检测时用）
    xref: int = 0                     # 图片 xref / 注释 xref
    stream_xref: int = 0              # 内容流 xref
    block_start: int = -1             # 在内容流中的字节区间
    block_end: int = -1
    annot_xref: int = 0
    image_name: str = ""
    has_alpha: bool = False

    @property
    def page_no(self) -> int:
        """给界面看的 1 基页码。"""
        return self.page + 1

    @property
    def bbox_rect(self) -> pymupdf.Rect:
        return pymupdf.Rect(*self.bbox)


@dataclass
class RemoveStats:
    text: int = 0
    image: int = 0
    annot: int = 0
    skipped: int = 0
    pages: set[int] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.text + self.image + self.annot

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (self.text, self.image, self.annot, self.skipped, len(self.pages))


# --------------------------------------------------------------------------- #
#  内容流解析
# --------------------------------------------------------------------------- #

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


@dataclass
class _Token:
    kind: str          # op / num / string / hex / name
    value: bytes
    start: int
    end: int


def _tokens(data: bytes) -> Iterable[_Token]:
    """够用的内容流 tokenizer：跳过注释，保留字符串/十六进制字符串。"""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x25:                       # % 注释
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == 0x28:                       # ( 字面字符串
            start, depth = i, 1
            i += 1
            while i < n and depth:
                ch = data[i]
                if ch == 0x5C:              # 反斜杠转义
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                i += 1
            yield _Token("string", data[start:i], start, i)
            continue
        if c == 0x3C:                       # < 十六进制字符串
            if i + 1 < n and data[i + 1] == 0x3C:
                yield _Token("op", b"<<", i, i + 2)
                i += 2
                continue
            j = data.find(b">", i + 1)
            if j < 0:
                j = n - 1
            yield _Token("hex", data[i:j + 1], i, j + 1)
            i = j + 1
            continue
        if c == 0x3E and i + 1 < n and data[i + 1] == 0x3E:
            yield _Token("op", b">>", i, i + 2)
            i += 2
            continue
        if c in b"[]":
            yield _Token("op", bytes((c,)), i, i + 1)
            i += 1
            continue
        if c == 0x2F:                       # /Name
            start = i
            i += 1
            while i < n and data[i] not in _WS + _DELIM:
                i += 1
            yield _Token("name", data[start:i], start, i)
            continue
        # 数字或操作符
        start = i
        while i < n and data[i] not in _WS + _DELIM:
            i += 1
        raw = data[start:i]
        try:
            float(raw)
            kind = "num"
        except ValueError:
            kind = "op"
        yield _Token(kind, raw, start, i)


def _pdf_literal(raw: bytes) -> bytes:
    """把 (…) 里的转义还原成字节。"""
    if len(raw) >= 2 and raw[:1] == b"(" and raw[-1:] == b")":
        raw = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(raw):
            break
        c = raw[i]
        mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                   0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
        if c in mapping:
            out.append(mapping[c])
            i += 1
        elif 0x30 <= c <= 0x37:
            digits = raw[i:i + 3]
            take = 0
            while take < len(digits) and 0x30 <= digits[take] <= 0x37:
                take += 1
            out.append(int(digits[:take], 8) & 0xFF)
            i += take
        elif c in (10, 13):
            i += 1
            if c == 13 and i < len(raw) and raw[i] == 10:
                i += 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _pdf_hex(raw: bytes) -> bytes:
    """把 <…> 里成对的十六进制字符还原成字节。"""
    body = raw.strip()
    if body[:1] == b"<":
        body = body[1:]
    if body[-1:] == b">":
        body = body[:-1]
    body = re.sub(rb"[^0-9A-Fa-f]", b"", body)
    if len(body) % 2:
        body += b"0"
    try:
        return bytes.fromhex(body.decode("ascii"))
    except ValueError:
        return b""


# --------------------------------------------------------------------------- #
#  ToUnicode CMap
# --------------------------------------------------------------------------- #

@dataclass
class _FontDecoder:
    mapping: dict[bytes, str] = field(default_factory=dict)
    code_len: int = 1

    def decode(self, raw: bytes) -> str:
        if not raw:
            return ""
        if not self.mapping:
            # 没有 ToUnicode 时只能做保守回退：简单字体按 cp1252，
            # 双字节 CID 字体按 UTF-16BE 尝试（很多扫描件恰好如此）。
            try:
                return raw.decode("cp1252")
            except Exception:
                pass
            if len(raw) % 2 == 0:
                try:
                    return raw.decode("utf-16-be")
                except Exception:
                    pass
            return raw.decode("latin-1", "replace")

        chunks = []
        step = max(1, self.code_len)
        for i in range(0, len(raw), step):
            code = raw[i:i + step]
            chunks.append(self.mapping.get(code, ""))
        if not any(chunks) and len(raw) % 2 == 0:
            # CMap 说 1 字节，但实际字体可能是 2 字节 —— 反过来再试一次
            chunks = [self.mapping.get(raw[i:i + 2], "")
                      for i in range(0, len(raw), 2)]
        return "".join(chunks)


def _utf16be(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-be")
    except Exception:
        return raw.decode("latin-1", "replace")


def _parse_cmap(data: bytes) -> _FontDecoder:
    """解析 ToUnicode CMap 的 bfchar / bfrange；够覆盖常见中文字体。"""
    text = data.decode("latin-1", "replace")
    decoder = _FontDecoder()

    # codespacerange 决定一个字符码占几字节
    space = re.search(r"begincodespacerange(.*?)endcodespacerange", text, re.S)
    if space:
        pair = re.search(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", space.group(1))
        if pair:
            decoder.code_len = max(1, len(pair.group(1)) // 2)

    def dst_value(token: str) -> str:
        token = token.strip()
        if token.startswith("<") and token.endswith(">"):
            return _utf16be(_pdf_hex(token.encode("ascii")))
        if token.startswith("(") and token.endswith(")"):
            return _pdf_literal(token.encode("latin-1")).decode("latin-1", "replace")
        return ""

    for body in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*(<[^>]*>|\([^)]*\))",
                                   body, re.S):
            decoder.mapping[bytes.fromhex(src)] = dst_value(dst)

    for body in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        # 1) <lo> <hi> <dstStart>
        for lo, hi, dst in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[^>]*>)", body, re.S):
            try:
                start, end = int(lo, 16), int(hi, 16)
            except ValueError:
                continue
            if end < start or end - start > 65535:
                continue
            base = _pdf_hex(dst.encode("ascii"))
            base_int = int.from_bytes(base, "big") if base else 0
            width = max(1, len(base))
            for offset, code in enumerate(range(start, end + 1)):
                value = (base_int + offset).to_bytes(width, "big")
                decoder.mapping[code.to_bytes(decoder.code_len, "big")] = \
                    _utf16be(value)
        # 2) <lo> <hi> [<dst1> <dst2> …]
        for lo, hi, arr in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[([^\]]*)\]",
                body, re.S):
            try:
                start = int(lo, 16)
            except ValueError:
                continue
            values = re.findall(r"<([0-9A-Fa-f]*)>", arr)
            for offset, value in enumerate(values):
                decoder.mapping[(start + offset).to_bytes(decoder.code_len, "big")] = \
                    _utf16be(_pdf_hex(f"<{value}>".encode("ascii")))
    return decoder


# --------------------------------------------------------------------------- #
#  流解析结果
# --------------------------------------------------------------------------- #

@dataclass
class _TextShow:
    """BT…ET 里的一次文字绘制操作（Tj / TJ / ' / "）。

    同一段 BT…ET 经常同时画出页眉、斜向水印和尺寸标注。整段删除会把
    尺寸标注一起带走，所以检测和删除都要精确到单次 show 操作。
    """

    stream_xref: int
    start: int                       # 操作数起点，删除到这个位置
    end: int                         # 操作符结束位置
    text: str
    font_name: str = ""
    font_size: float = 0.0
    color: tuple[float, float, float] | None = None
    opacity: float = 1.0
    char_start: int = 0
    char_end: int = 0
    trace_bbox: tuple[float, float, float, float] | None = None
    trace_index: int = -1


@dataclass
class _TextObject:
    stream_xref: int
    bt_start: int
    bt_end: int
    block: tuple[int, int] | None
    text: str
    font_name: str = ""
    font_size: float = 0.0
    color: tuple[float, float, float] | None = None
    opacity: float = 1.0
    trace_bbox: tuple[float, float, float, float] | None = None
    block_texts: list["_TextObject"] = field(default_factory=list)
    has_other_content: bool = False
    shows: list[_TextShow] = field(default_factory=list)


@dataclass
class _ImageObject:
    stream_xref: int
    block: tuple[int, int]
    name: str
    xref: int
    bbox: tuple[float, float, float, float] | None = None
    block_has_other: bool = False


@dataclass
class _StreamParse:
    text_objects: list[_TextObject] = field(default_factory=list)
    images: list[_ImageObject] = field(default_factory=list)
    blocks: list[tuple[int, int]] = field(default_factory=list)


def _font_resources(doc: pymupdf.Document, page: pymupdf.Page,
                    resources: str = "") -> dict[str, _FontDecoder]:
    """返回资源名 → ToUnicode 解码器。资源字典可以来自页面或 Form。"""
    names: dict[str, int] = {}
    # page.get_fonts(full=True) 覆盖页面及其 Form 中出现的字体，
    # 同一资源名可能对应不同字体；后出现的会覆盖，通常 Form 用独立名。
    try:
        for item in page.get_fonts(full=True):
            xref, _ext, _type, _base, name, *_ = item
            if name:
                names[name] = int(xref)
    except Exception:
        pass

    if resources:
        key = doc.xref_get_key(page.xref, "Resources")
        # 只用于兜底；Form 使用自身资源时由调用方传入完整字典字符串
        _ = key

    decoders: dict[str, _FontDecoder] = {}
    for name, xref in names.items():
        decoder = _FontDecoder()
        try:
            tu = doc.xref_get_key(xref, "ToUnicode")
            if tu and tu[0] == "xref":
                cmap_xref = int(tu[1].split()[0])
                decoder = _parse_cmap(doc.xref_stream(cmap_xref) or b"")
        except Exception:
            decoder = _FontDecoder()
        decoders[f"/{name}"] = decoder
        decoders[name] = decoder
    return decoders


def _parse_stream(doc: pymupdf.Document, stream_xref: int, data: bytes,
                  decoders: dict[str, _FontDecoder]) -> _StreamParse:
    """解析一条内容流：q/Q 块、BT/ET 文字、Do 图片。"""
    result = _StreamParse()
    stack: list[int] = []
    operands: list[_Token] = []
    in_text = False
    text_start = 0
    text_font = ""
    text_size = 0.0
    text_color: tuple[float, float, float] | None = None
    text_opacity = 1.0
    text_parts: list[str] = []
    text_strings: list[bytes] = []
    text_shows: list[_TextShow] = []
    text_char_count = 0
    array_start = -1                    # TJ 的 '[' 起点
    pending_text: list[_TextObject] = []
    pending_images: list[_ImageObject] = []

    for tok in _tokens(data):
        if tok.kind != "op":
            operands.append(tok)
            continue
        op = tok.value

        if op == b"q":
            stack.append(tok.start)
        elif op == b"Q":
            if stack:
                start = stack.pop()
                result.blocks.append((start, tok.end))
                # q 开始后创建的文字/图片，现在才知道完整块区间
                for obj in pending_text:
                    if obj.block and obj.block[0] == start and obj.block[1] < 0:
                        obj.block = (start, tok.end)
                for img in pending_images:
                    if img.block and img.block[0] == start and img.block[1] < 0:
                        img.block = (start, tok.end)
        elif op == b"BT":
            in_text = True
            text_start = tok.start
            text_parts = []
            text_strings = []
            text_shows = []
            text_char_count = 0
            array_start = -1
            text_font = ""
            text_size = 0.0
            text_color = None
            text_opacity = 1.0
        elif op == b"ET":
            if in_text:
                text = "".join(text_parts)
                block = (stack[-1], -1) if stack else None
                obj = _TextObject(
                    stream_xref=stream_xref,
                    bt_start=text_start,
                    bt_end=tok.end,
                    block=block,
                    text=text,
                    font_name=text_font,
                    font_size=text_size,
                    color=text_color,
                    opacity=text_opacity,
                    shows=text_shows,
                )
                result.text_objects.append(obj)
                if block:
                    pending_text.append(obj)
            in_text = False
            text_parts = []
            text_strings = []
            text_shows = []
            text_char_count = 0
            array_start = -1
        elif op == b"Tf":
            # /F0 12 Tf
            if len(operands) >= 2 and operands[-2].kind == "name":
                text_font = operands[-2].value.decode("latin-1").lstrip("/")
            if operands and operands[-1].kind == "num":
                try:
                    text_size = float(operands[-1].value)
                except ValueError:
                    pass
        elif op in (b"rg", b"RG", b"k", b"K", b"g", b"G", b"sc", b"scn"):
            vals = []
            for item in operands[-4:]:
                if item.kind == "num":
                    try:
                        vals.append(float(item.value))
                    except ValueError:
                        pass
            if len(vals) >= 3:
                text_color = (vals[-3], vals[-2], vals[-1])
            elif len(vals) == 1:
                text_color = (vals[0], vals[0], vals[0])
        elif op == b"gs":
            # 透明度在 ExtGState 里，内容流本身看不到 /ca 数值；
            # 这里只留个标记，真正的透明度从 texttrace 读取。
            pass
        elif op == b"[":
            array_start = tok.start
        elif op == b"Tj" or op == b"'" or op == b'"':
            if in_text and operands:
                item = None
                for candidate in reversed(operands):
                    if candidate.kind in ("string", "hex"):
                        item = candidate
                        break
                if item is not None:
                    raw = (_pdf_literal(item.value)
                           if item.kind == "string" else _pdf_hex(item.value))
                    decoded = _decode_strings([raw], decoders, text_font)[0]
                    # 双引号操作符前面还有字间距/词间距两个数字，一起删掉；
                    # 只删字符串会把两个裸数字留给下一个操作符。
                    start = operands[0].start if op == b'"' else item.start
                    show = _TextShow(
                        stream_xref=stream_xref,
                        start=start,
                        end=tok.end,
                        text=decoded,
                        font_name=text_font,
                        font_size=text_size,
                        color=text_color,
                        opacity=text_opacity,
                        char_start=text_char_count,
                        char_end=text_char_count + len(decoded),
                    )
                    text_shows.append(show)
                    text_char_count += len(decoded)
                    text_strings.append(raw)
                    text_parts.append(decoded)
        elif op == b"TJ":
            if in_text:
                pieces: list[bytes] = []
                piece_tokens: list[_Token] = []
                for item in operands:
                    if item.kind == "string":
                        pieces.append(_pdf_literal(item.value))
                        piece_tokens.append(item)
                    elif item.kind == "hex":
                        pieces.append(_pdf_hex(item.value))
                        piece_tokens.append(item)
                if pieces:
                    decoded = "".join(
                        _decode_strings(pieces, decoders, text_font))
                    start = (array_start if array_start >= 0
                             else piece_tokens[0].start)
                    text_shows.append(_TextShow(
                        stream_xref=stream_xref,
                        start=start,
                        end=tok.end,
                        text=decoded,
                        font_name=text_font,
                        font_size=text_size,
                        color=text_color,
                        opacity=text_opacity,
                        char_start=text_char_count,
                        char_end=text_char_count + len(decoded),
                    ))
                    text_char_count += len(decoded)
                    text_strings.extend(pieces)
                    text_parts.append(decoded)
                array_start = -1
        elif op == b"Do":
            if operands and operands[-1].kind == "name":
                name = operands[-1].value.decode("latin-1").lstrip("/")
                block = (stack[-1], -1) if stack else (tok.start, tok.end)
                img = _ImageObject(
                    stream_xref=stream_xref, block=block, name=name, xref=0)
                result.images.append(img)
                if stack:
                    pending_images.append(img)
        # 数组定界符不是操作符：不要清空操作数，否则 TJ 里的字符串
        # 会在读到 ] 时被提前丢掉。
        if op in (b"[", b"]", b"<<", b">>"):
            continue
        # 清空操作数：内容流里操作符之前的所有 token 都是它的参数
        operands = []
    return result


def _find_block_end(blocks: list[tuple[int, int]], start: int) -> int | None:
    for s, e in blocks:
        if s == start:
            return e
    return None


def _decode_strings(strings: list[bytes], decoders: dict[str, _FontDecoder],
                    font_name: str) -> list[str]:
    decoder = decoders.get(font_name) or decoders.get(f"/{font_name}") or _FontDecoder()
    return [decoder.decode(raw) for raw in strings]


# --------------------------------------------------------------------------- #
#  资源与文本 trace
# --------------------------------------------------------------------------- #

def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _trace_text(entry: dict) -> str:
    chars = entry.get("chars") or ()
    out = []
    for ch in chars:
        try:
            out.append(chr(int(ch[0])))
        except Exception:
            continue
    return "".join(out)


def _trace_bbox(entry: dict) -> tuple[float, float, float, float] | None:
    box = entry.get("bbox")
    if not box:
        return None
    return tuple(float(v) for v in box)


def _trace_rotation(entry: dict) -> float:
    direction = entry.get("dir")
    if not direction:
        return 0.0
    dx, dy = float(direction[0]), float(direction[1])
    return math.degrees(math.atan2(dy, dx))


def _norm_with_indices(text: str) -> tuple[str, list[int]]:
    """归一化文本，同时保留每个归一化字符在原串中的下标。"""
    chars: list[str] = []
    indices: list[int] = []
    for i, ch in enumerate(text or ""):
        if ch.isspace():
            continue
        chars.append(ch.lower())
        indices.append(i)
    return "".join(chars), indices


def _attach_show_traces(parsed: _PageParse, trace: list[dict]) -> None:
    """把每次文字 show 操作映射到 texttrace 的字符 bbox。

    同一段 BT…ET 里的多次 show 经常被 texttrace 合并成一条（本项目的
    图纸样本就是 ``confidentialconfidential``）。按内容流顺序逐段消费
    trace 字符，就能把每条 show 的 bbox 分开，而不是拿整块的外接矩形。
    """
    consumed: dict[int, int] = {}
    for obj in parsed.text_objects:
        for show in obj.shows:
            nshow, _ = _norm_with_indices(show.text)
            if not nshow:
                continue
            for ti, entry in enumerate(trace):
                ttext, mapping = _norm_with_indices(_trace_text(entry))
                if not ttext:
                    continue
                pos = ttext.find(nshow, consumed.get(ti, 0))
                if pos < 0:
                    continue
                first = mapping[pos]
                last = mapping[pos + len(nshow) - 1]
                chars = entry.get("chars") or ()
                bbox = None
                for ch in chars[first:last + 1]:
                    if len(ch) < 4 or not ch[3]:
                        continue
                    bb = tuple(float(v) for v in ch[3])
                    if bbox is None:
                        bbox = bb
                    else:
                        bbox = (min(bbox[0], bb[0]), min(bbox[1], bb[1]),
                                max(bbox[2], bb[2]), max(bbox[3], bb[3]))
                show.trace_bbox = bbox
                show.trace_index = ti
                show.opacity = float(entry.get("opacity", show.opacity) or show.opacity)
                show.font_size = float(entry.get("size", show.font_size)
                                       or show.font_size)
                color = entry.get("color")
                if color and len(color) >= 3:
                    show.color = tuple(float(c) for c in color[:3])
                if not show.font_name:
                    show.font_name = str(entry.get("font") or "")
                consumed[ti] = pos + len(nshow)
                break


def _query_matches(text: str, query: str) -> bool:
    if not query:
        return False
    ntext, nquery = _normalize(text), _normalize(query)
    if not nquery:
        return False
    if nquery in ntext:
        return True
    # 多行查询：任意一行足够长就认为命中
    for line in re.split(r"[\n\r]+", query):
        nline = _normalize(line)
        if len(nline) >= 2 and nline in ntext:
            return True
    return False


@dataclass
class _PageParse:
    text_objects: list[_TextObject] = field(default_factory=list)
    images: list[_ImageObject] = field(default_factory=list)
    blocks: set[tuple[int, int]] = field(default_factory=set)


def _parse_page(doc: pymupdf.Document, page: pymupdf.Page) -> _PageParse:
    """解析页面的全部内容流（含 Form XObject 递归）。"""
    out = _PageParse()
    decoders = _font_resources(doc, page)

    def parse_one(stream_xref: int, data: bytes) -> None:
        parsed = _parse_stream(doc, stream_xref, data, decoders)
        out.text_objects.extend(parsed.text_objects)
        out.blocks.update(parsed.blocks)
        # 图片 Do 的 xref 需要从资源里查
        for img in parsed.images:
            img.xref = _resolve_xobject(doc, page, img.name)
            out.images.append(img)

    for xref in page.get_contents():
        try:
            data = doc.xref_stream(xref)
        except Exception:
            data = b""
        if data:
            parse_one(xref, data)

    # Form XObject 单独展开。顺序上排在页面流之后，对「按文字匹配」
    # 已经足够；真正的删除仍落在 Form 自己的内容流上。
    for xref in _all_form_xrefs(doc, page):
        try:
            data = doc.xref_stream(xref)
        except Exception:
            continue
        if data:
            parse_one(xref, data)
    return out


def _resolve_xobject(doc: pymupdf.Document, page: pymupdf.Page,
                     name: str, form_only: bool = False) -> int:
    try:
        kind, value = doc.xref_get_key(page.xref, f"Resources/XObject/{name}")
        if kind == "xref":
            xref = int(value.split()[0])
            if form_only:
                sub = doc.xref_get_key(xref, "Subtype")
                if not sub or sub[1] != "/Form":
                    return 0
            return xref
    except Exception:
        pass
    # 页面资源里没有，再到所有 Form 的资源里找同名
    for form_xref in _all_form_xrefs(doc, page):
        try:
            kind, value = doc.xref_get_key(form_xref, f"Resources/XObject/{name}")
            if kind == "xref":
                return int(value.split()[0])
        except Exception:
            continue
    return 0


def _form_names(doc: pymupdf.Document, xref: int) -> list[str]:
    """某个资源字典里的 Form XObject 名称。"""
    names: list[str] = []
    try:
        kind, value = doc.xref_get_key(xref, "Resources/XObject")
        if kind == "dict":
            names += re.findall(r"/([^\s/<>]+)\s+\d+\s+0\s+R", value)
    except Exception:
        pass
    return list(dict.fromkeys(names))


def _all_form_xrefs(doc: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    """页面资源及其 Form 资源中所有 Form XObject 的 xref（递归）。"""
    out: list[int] = []
    queue = [page.xref]
    seen_owners: set[int] = set()
    while queue:
        owner = queue.pop(0)
        if owner in seen_owners:
            continue
        seen_owners.add(owner)
        for name in _form_names(doc, owner):
            try:
                kind, value = doc.xref_get_key(owner, f"Resources/XObject/{name}")
            except Exception:
                continue
            if kind != "xref":
                continue
            xref = int(value.split()[0])
            subtype = doc.xref_get_key(xref, "Subtype")
            if not subtype or subtype[1] != "/Form":
                continue
            if xref not in out:
                out.append(xref)
                queue.append(xref)
    return out


def _attach_trace(parsed: _PageParse, trace: list[dict]) -> None:
    """把 texttrace 的 bbox/颜色/透明度贴回解析出的文字对象。

    按内容流顺序 + 归一化文本匹配。匹配不上时保留 None，后续对象仍可
    在预览里显示，但不会自动删除（宁可漏删，不可误删正文）。
    """
    used: set[int] = set()
    pointer = 0
    for obj in parsed.text_objects:
        ntext = _normalize(obj.text)
        if not ntext:
            continue
        found = None
        for idx in range(pointer, len(trace)):
            if idx in used:
                continue
            ttext = _normalize(_trace_text(trace[idx]))
            if not ttext:
                continue
            if ntext == ttext or ntext.startswith(ttext) or ttext.startswith(ntext):
                found = idx
                break
            # 文字对象可能被拆成多个 span：尝试拼接后续连续 span
            combo = ttext
            for j in range(idx + 1, min(idx + 6, len(trace))):
                combo += _normalize(_trace_text(trace[j]))
                if combo == ntext:
                    found = idx
                    break
                if len(combo) > len(ntext):
                    break
            if found is not None:
                break
        if found is None:
            continue
        # 合并连续 trace span，直到文本长度与对象接近
        parts = []
        bbox = None
        idx = found
        while idx < len(trace) and idx not in used:
            ttext = _normalize(_trace_text(trace[idx]))
            if not ttext:
                idx += 1
                continue
            if parts and len("".join(parts)) >= len(ntext):
                break
            parts.append(ttext)
            used.add(idx)
            bb = _trace_bbox(trace[idx])
            if bb:
                bbox = bb if bbox is None else (
                    min(bbox[0], bb[0]), min(bbox[1], bb[1]),
                    max(bbox[2], bb[2]), max(bbox[3], bb[3]))
            if "".join(parts) == ntext:
                idx += 1
                break
            idx += 1
        pointer = max(pointer, idx)
        obj.trace_bbox = bbox
        if found < len(trace):
            entry = trace[found]
            obj.opacity = float(entry.get("opacity", 1.0) or 1.0)
            color = entry.get("color")
            if color and len(color) >= 3:
                obj.color = tuple(float(c) for c in color[:3])
            if not obj.font_name:
                obj.font_name = str(entry.get("font") or "")

    # 给每个 q/Q 块挂上块内文字，供安全判断
    by_block: dict[tuple[int, int], list[_TextObject]] = {}
    for obj in parsed.text_objects:
        if obj.block:
            by_block.setdefault(obj.block, []).append(obj)
    for objs in by_block.values():
        for obj in objs:
            obj.block_texts = objs


# --------------------------------------------------------------------------- #
#  检测
# --------------------------------------------------------------------------- #

class WatermarkDetector:
    """检测 PDF 中的文字、图片与注释水印。"""

    def __init__(self, query: str = "", auto: bool = False,
                 pages: list[int] | None = None):
        self.query = (query or "").strip()
        self.auto = auto or not self.query
        self.pages = pages

    def detect(self, doc: pymupdf.Document) -> list[WatermarkObject]:
        objects: list[WatermarkObject] = []
        target_pages = self.pages if self.pages is not None else range(doc.page_count)
        for pno in target_pages:
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc[pno]
            objects.extend(self._detect_page(doc, page, pno))
        return objects

    def _detect_page(self, doc: pymupdf.Document, page: pymupdf.Page,
                     pno: int) -> list[WatermarkObject]:
        out: list[WatermarkObject] = []
        try:
            trace = page.get_texttrace()
        except Exception:
            trace = []
        parsed = _parse_page(doc, page)
        _attach_trace(parsed, trace)
        _attach_show_traces(parsed, trace)

        try:
            annots = list(page.annots())
        except Exception:
            annots = []
        annot_rects = [annot.rect for annot in annots]

        # 按关键词检测时，精确到单次 Tj/TJ 操作。这样同一 BT…ET 里的
        # 页眉、两处斜向水印和尺寸标注可以只删水印，不碰标注。
        show_objects: list[WatermarkObject] = []
        if self.query:
            show_objects = self._text_objects_for_shows(page, parsed, trace, pno)
            out.extend(show_objects)

        # 文字：trace 是页面显示顺序，能直接拿到 bbox/透明度
        for idx, entry in enumerate(trace):
            if show_objects:
                break
            text = _trace_text(entry)
            if not text.strip():
                continue
            # FreeText / Stamp 注释的文字也会出现在 texttrace 里；
            # 注释由下面的注释分支处理，这里跳过避免重复列出。
            bb = _trace_bbox(entry)
            if bb and any(_rect_inside(pymupdf.Rect(bb), ar) for ar in annot_rects):
                continue
            if self.query:
                if not _query_matches(text, self.query):
                    continue
                confidence = "high"
            else:
                if not self._auto_text_candidate(text, entry, trace):
                    continue
                confidence = ("high" if float(entry.get("opacity", 1.0) or 1.0) < 0.6
                              else "medium")
            obj = self._text_object_for_trace(parsed, trace, idx, text, pno)
            if obj is None:
                bb = _trace_bbox(entry)
                if bb is None:
                    continue
                out.append(WatermarkObject(
                    oid=f"text:{pno}:none:{idx}", page=pno, kind="text", bbox=bb,
                    text=text, font=str(entry.get("font") or ""),
                    size=float(entry.get("size", 0) or 0),
                    color=tuple(float(c) for c in (entry.get("color") or (0, 0, 0))[:3]),
                    opacity=float(entry.get("opacity", 1.0) or 1.0),
                    confidence="low", removable=False,
                    note="未定位到内容流对象，可在预览中点选",
                ))
                continue
            out.append(obj)

        # 图片：排除整页扫描底图；跨页重复或带 alpha 的小图更可信。
        # 按关键词去水印时完全跳过图片：图纸里的 3D 轴测图、照片、Logo
        # 都只是普通内容，不能因为查询词恰好落在附近就被当成水印删掉。
        try:
            infos = [] if self.query else page.get_image_info(xrefs=True)
        except Exception:
            infos = []
        for idx, info in enumerate(infos):
            bbox = info.get("bbox")
            if not bbox:
                continue
            rect = pymupdf.Rect(bbox)
            if rect.width <= 1 or rect.height <= 1:
                continue
            area_ratio = (rect.width * rect.height) / max(1.0, page.rect.width * page.rect.height)
            if area_ratio >= 0.55:
                continue                  # 整页扫描件：不当作水印
            xref = int(info.get("xref") or 0)
            repeat = _image_repeat_count(doc, xref) if xref else 1
            has_alpha = bool(info.get("has-alpha") or info.get("smask"))
            confidence = "high" if (repeat > 1 or has_alpha) else "medium"
            if self.query and not self._image_near_query(page, rect, trace):
                # 有查询词时，与文字水印不重叠的小图降为低置信度，
                # 但仍列出来；它可能是与文字水印配套的 Logo。
                confidence = "low" if confidence == "medium" else confidence
            obj = self._image_object_for_info(parsed, pno, xref, rect, confidence)
            if obj is None:
                out.append(WatermarkObject(
                    oid=f"image:{pno}:{xref}:{idx}", page=pno, kind="image",
                    bbox=tuple(rect), xref=xref, has_alpha=has_alpha,
                    confidence="low", removable=False,
                    note="未定位到图片绘制块",
                ))
            else:
                out.append(obj)

        # 注释：FreeText / Stamp / Watermark，或内容命中查询
        for annot in annots:
            info = annot.info or {}
            content = str(info.get("content") or "")
            atype = annot.type[1] if annot.type else ""
            if self.query:
                if not _query_matches(content, self.query) and atype not in (
                        "FreeText", "Stamp", "Watermark"):
                    continue
            else:
                if atype not in ("FreeText", "Stamp", "Watermark"):
                    continue
            rect = annot.rect
            out.append(WatermarkObject(
                oid=f"annot:{pno}:{annot.xref}", page=pno, kind="annot",
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                text=content or atype, annot_xref=annot.xref,
                confidence="high" if content and self.query else "medium",
                note=atype,
            ))
        return out

    def _text_objects_for_shows(self, page: pymupdf.Page, parsed: _PageParse,
                                trace: list[dict],
                                pno: int) -> list[WatermarkObject]:
        """把命中查询词的每一次文字 show 操作变成独立可删对象。"""
        out: list[WatermarkObject] = []
        nquery = _normalize(self.query)
        for obj in parsed.text_objects:
            for show in obj.shows:
                if not _query_matches(show.text, self.query):
                    continue
                if show.end <= show.start:
                    continue
                bbox = show.trace_bbox or obj.trace_bbox
                if bbox is None:
                    hits = page.search_for(show.text)
                    if hits:
                        r = hits[0]
                        bbox = (r.x0, r.y0, r.x1, r.y1)
                if bbox is None:
                    continue
                exact = _normalize(show.text) == nquery
                out.append(WatermarkObject(
                    oid=f"text:{pno}:{show.stream_xref}:{show.start}",
                    page=pno,
                    kind="text",
                    bbox=bbox,
                    text=show.text,
                    font=show.font_name or obj.font_name,
                    size=show.font_size or obj.font_size,
                    color=show.color or obj.color,
                    opacity=show.opacity,
                    confidence="high" if exact else "medium",
                    removable=True,
                    note=("仅删除该次文字绘制，同页正文保留" if exact
                          else "删除包含关键词的整段文字，同页正文保留"),
                    xref=show.stream_xref,
                    stream_xref=show.stream_xref,
                    block_start=show.start,
                    block_end=show.end,
                ))
        return out

    def _auto_text_candidate(self, text: str, entry: dict, trace: list[dict]) -> bool:
        if len(_normalize(text)) < 2:
            return False
        opacity = float(entry.get("opacity", 1.0) or 1.0)
        if opacity < 0.9:
            return True
        if abs(_trace_rotation(entry)) > 5:
            return True
        # 重复出现的大字号文字：页眉/页脚也会命中，但会以 medium 置信度
        # 展示出来，用户可取消勾选，不会静默删除。
        norm = _normalize(text)
        if len(norm) <= 40:
            same = sum(1 for e in trace if _normalize(_trace_text(e)) == norm)
            size = float(entry.get("size", 0) or 0)
            if same >= 2 and size >= 18:
                return True
        return False

    def _text_object_for_trace(self, parsed: _PageParse, trace: list[dict],
                               idx: int, text: str, pno: int) -> WatermarkObject | None:
        """为一条 texttrace 找到对应的内容流文字对象。"""
        ntext = _normalize(text)
        candidates = [o for o in parsed.text_objects
                      if _normalize(o.text) and (
                          ntext in _normalize(o.text) or
                          _normalize(o.text) in ntext)]
        if not candidates:
            return None
        # 优先取 bbox 与 trace 最接近的
        bb = _trace_bbox(trace[idx])
        chosen = candidates[0]
        if bb:
            best = float("inf")
            for obj in candidates:
                if not obj.trace_bbox:
                    continue
                dist = sum(abs(a - b) for a, b in zip(obj.trace_bbox, bb))
                if dist < best:
                    best, chosen = dist, obj
        safe = self._safe_text_block(chosen) and bool(chosen.block)
        start, end = chosen.block if chosen.block else (-1, -1)
        note = ""
        confidence = "high" if safe else "low"
        if not safe and chosen.bt_end > chosen.bt_start:
            # 很多 PDF 根本不把正文/水印包在 q…Q 里；只要这个 BT…ET
            # 只包含当前这一条文字，就可以安全地只删这段，不动同页正文。
            if _normalize(chosen.text) == ntext:
                start, end = chosen.bt_start, chosen.bt_end
                safe = True
                confidence = "medium"
                note = "仅删除该段水印文字，同页正文保留"
            elif not chosen.block:
                note = "未定位到 q…Q 内容块，可在预览中点选"
            else:
                note = "同一内容块中混有其它文字，已跳过"
        elif not safe:
            note = "未定位到可安全删除的内容块"
        return WatermarkObject(
            oid=f"text:{pno}:{chosen.stream_xref}:{chosen.bt_start}",
            page=pno, kind="text",
            bbox=chosen.trace_bbox or bb or (0, 0, 0, 0),
            text=text, font=chosen.font_name or str(trace[idx].get("font") or ""),
            size=chosen.font_size, color=chosen.color,
            opacity=chosen.opacity, confidence=confidence,
            removable=safe,
            note=note,
            xref=chosen.stream_xref,
            stream_xref=chosen.stream_xref,
            block_start=start,
            block_end=end,
        )

    def _safe_text_block(self, obj: _TextObject) -> bool:
        if not obj.block:
            return False
        peers = obj.block_texts
        if len(peers) <= 1:
            return True
        # 同一块内若还有不同字号/字重/文本内容的文字，基本可以认定
        # 是正文，跳过。宁可漏删，也不要把正文整块带走。
        own = _normalize(obj.text)
        for peer in peers:
            if peer is obj:
                continue
            if abs(peer.font_size - obj.font_size) > 0.6:
                return False
            if peer.font_name and obj.font_name and peer.font_name != obj.font_name:
                return False
            ptext = _normalize(peer.text)
            if not (ptext == own or ptext in own or own in ptext):
                return False
        return True

    def _image_near_query(self, page: pymupdf.Page, rect: pymupdf.Rect,
                          trace: list[dict]) -> bool:
        for entry in trace:
            if not _query_matches(_trace_text(entry), self.query):
                continue
            bb = _trace_bbox(entry)
            if not bb:
                continue
            if pymupdf.Rect(bb).intersects(rect):
                return True
        return False

    def _image_object_for_info(self, parsed: _PageParse, pno: int, xref: int,
                               rect: pymupdf.Rect, confidence: str) -> WatermarkObject | None:
        for img in parsed.images:
            if xref and img.xref and img.xref != xref:
                continue
            if not img.block:
                continue
            # 块内若还有文字，说明这不是纯粹的图片绘制块
            has_text = any(o.block == img.block for o in parsed.text_objects)
            safe = not has_text
            bbox = tuple(rect)
            return WatermarkObject(
                oid=f"image:{pno}:{img.stream_xref}:{img.block[0]}",
                page=pno, kind="image", bbox=bbox, xref=xref,
                image_name=img.name, confidence=confidence if safe else "low",
                removable=safe,
                note="" if safe else "图片与文字在同一内容块，已跳过",
                stream_xref=img.stream_xref,
                block_start=img.block[0], block_end=img.block[1],
            )
        return None


def _rect_inside(inner: pymupdf.Rect, outer: pymupdf.Rect) -> bool:
    if inner.is_empty or outer.is_empty:
        return False
    inter = pymupdf.Rect(inner)
    inter.intersect(outer)
    if inter.is_empty:
        return False
    area = max(1.0, inner.width * inner.height)
    return (inter.width * inter.height) / area >= 0.6


def _image_repeat_count(doc: pymupdf.Document, xref: int) -> int:
    if not xref:
        return 0
    count = 0
    for pno in range(doc.page_count):
        try:
            for info in doc[pno].get_image_info(xrefs=True):
                if int(info.get("xref") or 0) == xref:
                    count += 1
                    break
        except Exception:
            continue
    return count


# --------------------------------------------------------------------------- #
#  删除
# --------------------------------------------------------------------------- #

class WatermarkRemover:
    """按检测结果删除对象；调用方负责打开/保存文档。"""

    def remove(self, doc: pymupdf.Document,
               objects: Iterable[WatermarkObject]) -> RemoveStats:
        stats = RemoveStats()
        edits: dict[int, list[tuple[int, int]]] = {}
        annots: dict[int, set[int]] = {}
        seen: set[tuple[int, int, int]] = set()

        for obj in objects:
            if not obj.removable:
                stats.skipped += 1
                continue
            if obj.kind == "annot":
                annots.setdefault(obj.page, set()).add(obj.annot_xref)
                continue
            if obj.kind in ("text", "image"):
                if obj.stream_xref <= 0 or obj.block_start < 0 or obj.block_end <= obj.block_start:
                    stats.skipped += 1
                    continue
                key = (obj.stream_xref, obj.block_start, obj.block_end)
                if key in seen:
                    continue
                seen.add(key)
                edits.setdefault(obj.stream_xref, []).append(
                    (obj.block_start, obj.block_end))
                if obj.kind == "text":
                    stats.text += 1
                else:
                    stats.image += 1
                stats.pages.add(obj.page + 1)

        # 删除注释
        for pno, xrefs in annots.items():
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc[pno]
            for annot in list(page.annots() or []):
                if annot.xref in xrefs:
                    try:
                        page.delete_annot(annot)
                        stats.annot += 1
                        stats.pages.add(pno + 1)
                    except Exception:
                        stats.skipped += 1

        # 删除内容流块：同一流从后往前删，避免偏移
        for xref, ranges in edits.items():
            try:
                data = doc.xref_stream(xref)
            except Exception:
                stats.skipped += len(ranges)
                continue
            if not data:
                stats.skipped += len(ranges)
                continue
            # 合并重叠区间
            ranges = sorted(set(ranges), reverse=True)
            new_data = data
            last_start = len(data) + 1
            for start, end in ranges:
                if end >= last_start:
                    # 与前一个要删的区间重叠，合并
                    start = min(start, last_start)
                if start < 0 or end > len(new_data):
                    stats.skipped += 1
                    continue
                new_data = new_data[:start] + new_data[end:]
                last_start = start
            try:
                doc.update_stream(xref, new_data, compress=True)
            except Exception:
                stats.skipped += len(ranges)
        return stats
