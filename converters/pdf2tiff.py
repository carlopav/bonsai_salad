#!/usr/bin/env python3
"""
Converte un PDF in TIFF preservando la risoluzione originale.

Per i PDF da scansione rileva il DPI effettivo delle immagini incorporate
in ogni pagina e renderizza a quella risoluzione (nessun ricampionamento).
Per i PDF vettoriali (nessuna immagine rilevata) usa il DPI di fallback.

Uso:
    python pdf2tiff.py                              # finestra di scelta file
    python pdf2tiff.py input.pdf                    # -> input.tiff (multipagina)
    python pdf2tiff.py input.pdf -o uscita.tiff
    python pdf2tiff.py input.pdf --separate         # un TIFF per pagina
    python pdf2tiff.py input.pdf --dpi 600          # forza il DPI
    python pdf2tiff.py input.pdf --compression jpeg # tiff_lzw (default) | jpeg | none

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

Image.MAX_IMAGE_PIXELS = None  # le scansioni grandi superano il limite di default

FALLBACK_DPI = 300


def ask_input_file() -> Path | None:
    """Apre la finestra di sistema per scegliere il PDF da convertire."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # porta la finestra in primo piano
    path = filedialog.askopenfilename(
        title="Scegli il PDF da convertire in TIFF",
        filetypes=[("File PDF", "*.pdf"), ("Tutti i file", "*.*")],
    )
    root.destroy()
    return Path(path) if path else None


def detect_page_dpi(page: pymupdf.Page) -> float | None:
    """Stima il DPI effettivo della pagina dalle immagini incorporate.

    Confronta i pixel dell'immagine incorporata con le dimensioni in punti
    (1 pt = 1/72") dell'area che occupa sulla pagina. Restituisce il DPI
    massimo trovato, oppure None se la pagina non contiene immagini.
    """
    best = None
    for img in page.get_images(full=True):
        xref = img[0]
        width_px, height_px = img[2], img[3]
        for rect in page.get_image_rects(xref):
            if rect.width < 1 or rect.height < 1:
                continue
            dpi_x = width_px * 72.0 / rect.width
            dpi_y = height_px * 72.0 / rect.height
            dpi = max(dpi_x, dpi_y)
            if best is None or dpi > best:
                best = dpi
    return best


def render_page(page: pymupdf.Page, dpi: float) -> Image.Image:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    img.load()
    img.info["dpi"] = (round(dpi), round(dpi))
    return img


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                     epilog="Il DPI viene rilevato pagina per pagina dalle immagini incorporate.")
    parser.add_argument("input", type=Path, nargs="?", default=None,
                        help="file PDF di ingresso (se omesso si apre una finestra di scelta)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="file TIFF di uscita (default: stesso nome del PDF)")
    parser.add_argument("--dpi", type=float, default=None,
                        help="forza questo DPI invece di rilevarlo dal PDF")
    parser.add_argument("--fallback-dpi", type=float, default=FALLBACK_DPI,
                        help=f"DPI per pagine senza immagini incorporate (default {FALLBACK_DPI})")
    parser.add_argument("--max-dpi", type=float, default=1200,
                        help="limite superiore al DPI rilevato (default 1200)")
    parser.add_argument("--compression", choices=["tiff_lzw", "jpeg", "none"],
                        default="tiff_lzw", help="compressione TIFF (default tiff_lzw, senza perdita)")
    parser.add_argument("--separate", action="store_true",
                        help="salva un TIFF per pagina invece di un multipagina")
    args = parser.parse_args()

    if args.input is None:
        args.input = ask_input_file()
        if args.input is None:
            print("Nessun file selezionato.", file=sys.stderr)
            return 1

    if not args.input.is_file():
        print(f"Errore: file non trovato: {args.input}", file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(".tiff")
    doc = pymupdf.open(args.input)

    pages: list[Image.Image] = []
    dpis: list[float] = []
    for i, page in enumerate(doc, start=1):
        if args.dpi:
            dpi, origin = args.dpi, "forzato"
        else:
            detected = detect_page_dpi(page)
            if detected is None:
                dpi, origin = args.fallback_dpi, "fallback"
            else:
                dpi, origin = min(detected, args.max_dpi), "rilevato"
        img = render_page(page, dpi)
        pages.append(img)
        dpis.append(dpi)
        print(f"  pagina {i}/{doc.page_count}: {dpi:.0f} dpi ({origin}), {img.width}x{img.height} px")

    save_opts = {}
    if args.compression != "none":
        save_opts["compression"] = args.compression
        if args.compression == "jpeg":
            save_opts["quality"] = 90

    if args.separate:
        digits = len(str(doc.page_count))
        for i, (img, dpi) in enumerate(zip(pages, dpis), start=1):
            out = output.with_stem(f"{output.stem}_p{i:0{digits}d}")
            img.save(out, format="TIFF", dpi=(round(dpi), round(dpi)), **save_opts)
            print(f"Salvato: {out}")
    else:
        pages[0].save(output, format="TIFF", dpi=(round(dpis[0]), round(dpis[0])),
                      save_all=True, append_images=pages[1:], **save_opts)
        print(f"Salvato: {output} ({doc.page_count} pagine)")

    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
