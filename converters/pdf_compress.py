#!/usr/bin/env python3
"""
Comprime un PDF ricampionando a 150 dpi le immagini che stanno sopra.

Il dpi di un'immagine è quello con cui finisce sulla carta: i pixel che
contiene divisi per lo spazio che occupa nella pagina. Chi è già sotto la
soglia resta intatto, e resta intatto tutto ciò che è vettoriale — testi,
linee, retini. La trasparenza viene mantenuta: un'immagine con maschera è
ricampionata insieme alla sua maschera. A valle il file viene ripulito: font
ridotti ai caratteri usati, oggetti orfani buttati, stream ricompressi.

Uso:
    python pdf_compress.py                          # finestra di scelta file
    python pdf_compress.py input.pdf                # -> input_compresso.pdf
    python pdf_compress.py input.pdf -o uscita.pdf
    python pdf_compress.py input.pdf --dpi 200      # altra soglia
    python pdf_compress.py input.pdf --quality 60   # qualità JPEG (default 80)
    python pdf_compress.py input.pdf --gray         # immagini in scala di grigi
    python pdf_compress.py input.pdf --analyze      # elenca le immagini, non scrive nulla

Dipendenze: pymupdf e pillow — installate automaticamente al primo avvio se mancanti.
"""

import argparse
import io
import subprocess
import sys
from pathlib import Path


def _ensure_dependencies() -> None:
    """Installa con pip le dipendenze mancanti."""
    missing = []
    for module, package in [("pymupdf", "pymupdf"), ("PIL", "pillow")]:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"Installo le dipendenze mancanti: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


_ensure_dependencies()

import pymupdf  # PyMuPDF
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # le immagini di sfondo grandi superano il limite di default

TARGET_DPI = 150
JPEG_QUALITY = 80


def ask_input_file() -> Path | None:
    """Apre la finestra di sistema per scegliere il PDF da comprimere."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # porta la finestra in primo piano
    path = filedialog.askopenfilename(
        title="Scegli il PDF da comprimere",
        filetypes=[("File PDF", "*.pdf"), ("Tutti i file", "*.*")],
    )
    root.destroy()
    return Path(path) if path else None


def survey_images(doc: pymupdf.Document) -> list[dict]:
    """Le immagini del documento con il dpi a cui vengono stampate.

    Una stessa immagine può comparire più volte e a scale diverse: vale la
    collocazione più fitta, quella che il ricampionamento deve reggere.
    """
    found: dict[int, dict] = {}
    for number, page in enumerate(doc):
        for image in page.get_images(full=True):
            xref, smask, width, height = image[0], image[1], image[2], image[3]
            for rect in page.get_image_rects(xref):
                if rect.width < 1 or rect.height < 1:
                    continue
                dpi = max(width * 72.0 / rect.width, height * 72.0 / rect.height)
                previous = found.get(xref)
                if previous is None or dpi > previous["dpi"]:
                    found[xref] = {"page": number, "xref": xref, "smask": smask,
                                   "width": width, "height": height, "dpi": dpi}
    return sorted(found.values(), key=lambda image: -image["dpi"])


def _open(doc: pymupdf.Document, xref: int) -> Image.Image:
    picture = Image.open(io.BytesIO(doc.extract_image(xref)["image"]))
    picture.load()
    return picture


def resample(doc: pymupdf.Document, image: dict, dpi: float, quality: int, gray: bool) -> int:
    """Riscrive un'immagine alla risoluzione voluta, e dice quanti byte pesa ora."""
    scale = dpi / image["dpi"]
    size = (max(1, round(image["width"] * scale)), max(1, round(image["height"] * scale)))

    picture = _open(doc, image["xref"])
    if picture.mode in ("1", "P", "CMYK", "LA", "RGBA"):
        picture = picture.convert("L" if picture.mode == "1" else "RGB")
    if gray and picture.mode != "L":
        picture = picture.convert("L")

    if image["smask"]:
        mask = _open(doc, image["smask"]).convert("L")
        if mask.size != picture.size:
            mask = mask.resize(picture.size, Image.LANCZOS)
        picture = picture.convert("LA" if picture.mode == "L" else "RGBA")
        picture.putalpha(mask)

    picture = picture.resize(size, Image.LANCZOS)

    buffer = io.BytesIO()
    if picture.mode in ("LA", "RGBA"):
        picture.save(buffer, "PNG", optimize=True)  # il JPEG non porta la trasparenza
    else:
        picture.save(buffer, "JPEG", quality=quality, optimize=True)
    doc[image["page"]].replace_image(image["xref"], stream=buffer.getvalue())
    return buffer.getbuffer().nbytes


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("input", type=Path, nargs="?", default=None,
                        help="file PDF di ingresso (se omesso si apre una finestra di scelta)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="file PDF di uscita (default: <nome>_compresso.pdf)")
    parser.add_argument("--dpi", type=float, default=TARGET_DPI,
                        help=f"risoluzione di destinazione (default {TARGET_DPI})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY,
                        help=f"qualità JPEG delle immagini senza trasparenza (default {JPEG_QUALITY})")
    parser.add_argument("--gray", action="store_true",
                        help="converte le immagini in scala di grigi")
    parser.add_argument("--analyze", action="store_true",
                        help="elenca le immagini e il loro dpi senza scrivere nulla")
    args = parser.parse_args()

    if args.input is None:
        args.input = ask_input_file()
        if args.input is None:
            print("Nessun file selezionato.", file=sys.stderr)
            return 1

    if not args.input.is_file():
        print(f"Errore: file non trovato: {args.input}", file=sys.stderr)
        return 1

    doc = pymupdf.open(args.input)
    images = survey_images(doc)
    above = [image for image in images if image["dpi"] > args.dpi]

    print(f"{args.input.name}: {human(args.input.stat().st_size)}, {doc.page_count} pagine, "
          f"{len(images)} immagini, {len(above)} sopra {args.dpi:.0f} dpi")

    if args.analyze:
        for image in images:
            mark = "->" if image["dpi"] > args.dpi else "  "
            print(f"  {mark} pagina {image['page'] + 1}, xref {image['xref']}: "
                  f"{image['width']}x{image['height']} px, {image['dpi']:.0f} dpi"
                  f"{', con maschera' if image['smask'] else ''}")
        doc.close()
        return 0

    output = args.output or args.input.with_stem(args.input.stem + "_compresso")
    if output.resolve() == args.input.resolve():
        print("Errore: l'uscita coincide con l'ingresso.", file=sys.stderr)
        doc.close()
        return 1

    for image in above:
        try:
            written = resample(doc, image, args.dpi, args.quality, args.gray)
        except Exception as exc:  # un'immagine ostica non deve fermare le altre
            print(f"  pagina {image['page'] + 1}, xref {image['xref']}: lasciata intatta ({exc})")
            continue
        print(f"  pagina {image['page'] + 1}, xref {image['xref']}: "
              f"{image['dpi']:.0f} -> {args.dpi:.0f} dpi, {human(written)}")

    try:
        doc.subset_fonts()
    except Exception as exc:  # i font malformati non devono fermare la compressione
        print(f"  font lasciati interi: {exc}")

    doc.save(output, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
    doc.close()

    before, after = args.input.stat().st_size, output.stat().st_size
    print(f"Salvato: {output} — {human(before)} -> {human(after)} "
          f"({100 * (before - after) / before:.0f}% in meno)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
