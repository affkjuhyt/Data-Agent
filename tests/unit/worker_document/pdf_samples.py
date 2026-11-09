"""Small PDFs written by hand for the extractor's tests: text in Helvetica at given sizes and
places, a JPEG picture, a media and a crop box. Writing the file directly keeps the samples
independent of the libraries under test."""

from __future__ import annotations

from typing import TypedDict


class Text(TypedDict):
    x: float
    y: float
    size: float
    text: str


class Picture(TypedDict):
    x: float
    y: float
    width: float
    height: float
    jpeg: bytes
    pixels: tuple[int, int]


class PageSpec(TypedDict, total=False):
    texts: list[Text]
    picture: Picture
    media: tuple[float, float, float, float]
    crop: tuple[float, float, float, float]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _box(box: tuple[float, float, float, float]) -> str:
    return "[" + " ".join(f"{v:g}" for v in box) + "]"


def make_pdf(pages: list[PageSpec]) -> bytes:
    """A PDF with one page per spec; coordinates are PDF user space (origin bottom left)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in once the page tree's number is known
    tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids: list[int] = []
    for spec in pages:
        content = []
        for item in spec.get("texts", []):
            content.append(
                f"BT /F1 {item['size']:g} Tf {item['x']:g} {item['y']:g} Td "
                f"({_escape(item['text'])}) Tj ET"
            )
        resources = f"/Font << /F1 {font} 0 R >>"
        picture = spec.get("picture")
        if picture:
            width, height = picture["pixels"]
            image = add(
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(picture['jpeg'])} >>\nstream\n".encode()
                + picture["jpeg"]
                + b"\nendstream"
            )
            resources += f" /XObject << /Im1 {image} 0 R >>"
            content.append(
                f"q {picture['width']:g} 0 0 {picture['height']:g} {picture['x']:g} "
                f"{picture['y']:g} cm /Im1 Do Q"
            )
        stream = "\n".join(content).encode("latin-1")
        body = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        media = spec.get("media", (0, 0, 595, 842))
        crop = f" /CropBox {_box(spec['crop'])}" if "crop" in spec else ""
        kids.append(
            add(
                f"<< /Type /Page /Parent {tree} 0 R /MediaBox {_box(media)}{crop} "
                f"/Resources << {resources} >> /Contents {body} 0 R >>".encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    objects[tree - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
