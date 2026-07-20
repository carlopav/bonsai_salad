#!/usr/bin/env python3
"""
Crea un PDF da una o più immagini, una immagine per pagina.

Ogni pagina ha la stessa dimensione dell'immagine: la misura fisica in punti
(1 pt = 1/72") viene calcolata dai pixel dell'immagine e dal suo DPI. Le
immagini vengono inserite nell'ordine indicato (o in ordine alfabetico se
scelte dalla finestra di sistema).

Uso:
    python images2pdf.py                                 # finestra di scelta file
    python images2pdf.py a.jpg b.png -o album.pdf        # -> album.pdf
    python images2pdf.py *.tiff                           # -> primo_nome.pdf
    python images2pdf.py a.jpg --dpi 300                 # forza il DPI (dimensione pagina)

Dipendenze: pillow — installata automaticamente al primo avvio se mancante.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def _ensure_dependencies() -> None:
    """Installa con pip le dipendenze mancanti."""
    missing = []
    for module, package in [("PIL", "pillow")]:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"Installo le dipendenze mancanti: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


_ensure_dependencies()

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # le scansioni grandi superano il limite di default

FALLBACK_DPI = 300


def ask_input_files() -> list[Path]:
    """Apre la finestra di sistema per scegliere le immagini da unire."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # porta la finestra in primo piano
    paths = filedialog.askopenfilenames(
        title="Scegli le immagini da unire in un PDF",
        filetypes=[
            ("Immagini", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.gif *.webp"),
            ("Tutti i file", "*.*"),
        ],
    )
    root.destroy()
    return sorted(Path(p) for p in paths)


def load_page(path: Path, dpi: float | None, fallback_dpi: float) -> Image.Image:
    """Apre un'immagine come pagina RGB con il DPI risolto nei metadati."""
    img = Image.open(path)
    img.load()
    if dpi is None:
        img_dpi = img.info.get("dpi")
        dpi = float(img_dpi[0]) if img_dpi and img_dpi[0] else fallback_dpi
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    # Pillow deriva la dimensione fisica della pagina PDF dal DPI in encoderinfo,
    # una pagina per immagine; info["dpi"] resta solo per la stampa a video.
    img.info["dpi"] = (dpi, dpi)
    img.encoderinfo = {"dpi": (dpi, dpi)}
    return img


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("inputs", type=Path, nargs="*", default=None,
                        help="immagini di ingresso (se omesse si apre una finestra di scelta)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="file PDF di uscita (default: nome della prima immagine)")
    parser.add_argument("--dpi", type=float, default=None,
                        help="forza questo DPI per tutte le pagine invece di leggerlo dalle immagini")
    parser.add_argument("--fallback-dpi", type=float, default=FALLBACK_DPI,
                        help=f"DPI per immagini senza metadato di risoluzione (default {FALLBACK_DPI})")
    args = parser.parse_args()

    inputs = args.inputs or ask_input_files()
    if not inputs:
        print("Nessun file selezionato.", file=sys.stderr)
        return 1

    missing = [p for p in inputs if not p.is_file()]
    if missing:
        for p in missing:
            print(f"Errore: file non trovato: {p}", file=sys.stderr)
        return 1

    pages: list[Image.Image] = []
    for i, path in enumerate(inputs, start=1):
        img = load_page(path, args.dpi, args.fallback_dpi)
        pages.append(img)
        dpi = img.info["dpi"][0]
        w_mm = img.width / dpi * 25.4
        h_mm = img.height / dpi * 25.4
        print(f"  pagina {i}/{len(inputs)}: {path.name} — {img.width}x{img.height} px "
              f"@ {dpi:.0f} dpi ({w_mm:.0f}x{h_mm:.0f} mm)")

    output = args.output or inputs[0].with_suffix(".pdf")
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:])
    print(f"Salvato: {output} ({len(pages)} pagine)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
