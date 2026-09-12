# Bonsai Salad — invarianza_idraulica tool

"""La planimetria delle superfici conteggiate, in TIFF, da incollare in
relazione.

Disegna le geometrie che hanno prodotto i numeri — dopo sovrapposizione e
ritaglio — quindi mostra ciò che è stato contato, non ciò che è modellato: è la
sola verifica visiva possibile di un calcolo che altrimenti resta una colonna
di cifre.

Il colore segue il coefficiente di deflusso su una rampa continua, blu dove
l'acqua si infiltra e rosso dove defluisce tutta: il disegno si legge senza
consultare la tabella. Blu-giallo-rosso invece del più consueto verde-rosso per
la stampa e per i daltonismi sul rosso-verde.
"""

import io

import numpy as np

DPI = 300
WIDTH_CM = 17.0
MAX_HEIGHT_CM = 24.0
COLORMAP = "RdYlBu_r"
CM_PER_INCH = 2.54

# Righe diverse con lo stesso coefficiente avrebbero lo stesso colore: si
# distinguono per tratteggio, e solo dove la collisione esiste davvero.
HATCHES = ("//", "\\\\", "xx", "..", "++", "oo")

SCALE_STEPS = (1, 2, 5)

# Fascia sotto il perimetro riservata alla barra di scala, in frazioni
# dell'altezza dell'ambito.
SCALE_BAND = 0.12


def hatches(rows):
    """{indice di riga: tratteggio}. Vuoto per i coefficienti usati una volta
    sola: un tratteggio senza collisione da distinguere sporcherebbe e basta."""
    seen = {}
    for index, row in enumerate(rows):
        seen.setdefault(row.coefficient, []).append(index)
    assigned = {}
    for indexes in seen.values():
        if len(indexes) == 1:
            assigned[indexes[0]] = ""
            continue
        for position, index in enumerate(indexes):
            assigned[index] = HATCHES[position % len(HATCHES)]
    return assigned


def scale_length(width):
    """La lunghezza tonda più vicina a un quinto della larghezza: 1, 2 o 5 per
    una potenza di dieci, come si disegna una barra di scala."""
    target = width / 5.0
    magnitude = 10 ** np.floor(np.log10(max(target, 1e-6)))
    for step in SCALE_STEPS:
        if step * magnitude >= target:
            return step * magnitude
    return 10 * magnitude


def _path(polygon, Path):
    vertices, codes = [], []
    for ring in (polygon.exterior, *polygon.interiors):
        coordinates = np.asarray(ring.coords)
        vertices.append(coordinates)
        codes.append([Path.MOVETO] + [Path.LINETO] * (len(coordinates) - 2) + [Path.CLOSEPOLY])
    return Path(np.concatenate(vertices), np.concatenate(codes))


def _parts(geometry):
    return getattr(geometry, "geoms", (geometry,))


def render(measurement, path, title, true_north=None):
    """Scrive il TIFF. False se matplotlib o Pillow mancano: la tabella non
    dipende dal disegno, e far fallire tutto per il disegno sarebbe peggio del
    disegno mancante."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        from matplotlib.patches import PathPatch, Rectangle
        from matplotlib.path import Path
        from PIL import Image
    except ImportError:
        return False

    minx, miny, maxx, maxy = measurement.boundary.bounds
    width, height = maxx - minx, maxy - miny
    figure_width = WIDTH_CM / CM_PER_INCH
    figure_height = min(figure_width * height / width if width else figure_width, MAX_HEIGHT_CM / CM_PER_INCH)

    figure, axes = pyplot.subplots(figsize=(figure_width, figure_height))
    colormap, norm = pyplot.get_cmap(COLORMAP), Normalize(0.0, 1.0)
    assigned = hatches(measurement.rows)

    for index, row in enumerate(measurement.rows):
        for part in _parts(row.geometry):
            if part.is_empty:
                continue
            axes.add_patch(
                PathPatch(
                    _path(part, Path),
                    facecolor=colormap(norm(row.coefficient)),
                    edgecolor="white",
                    linewidth=0.3,
                    hatch=assigned[index] or None,
                )
            )
    for part in _parts(measurement.boundary):
        axes.plot(*part.exterior.xy, color="black", linewidth=1.5)

    axes.set_aspect("equal")
    axes.axis("off")
    axes.set_title(title)
    # Limiti espliciti invece di autoscale: la barra di scala sta sotto il
    # perimetro, e senza spazio riservato gli assi la taglierebbero via.
    margin = max(width, height) * 0.02
    axes.set_xlim(minx - margin, maxx + margin)
    axes.set_ylim(miny - height * SCALE_BAND, maxy + margin)

    bar = scale_length(width)
    thickness = height * 0.012
    baseline = miny - height * (SCALE_BAND * 0.55)
    axes.add_patch(Rectangle((minx, baseline), bar, thickness, facecolor="black"))
    axes.text(minx + bar / 2, baseline - thickness, f"{bar:g} m", ha="center", va="top", fontsize=7)

    if true_north is not None:
        axes.annotate(
            "N",
            xy=(maxx, maxy),
            xytext=(maxx - np.sin(true_north) * height * 0.12, maxy - np.cos(true_north) * height * 0.12),
            arrowprops={"arrowstyle": "->", "color": "black"},
            ha="center",
            fontsize=8,
        )

    colorbar = figure.colorbar(
        ScalarMappable(norm=norm, cmap=colormap),
        ax=axes,
        orientation="horizontal",
        fraction=0.04,
        pad=0.02,
        shrink=0.5,
    )
    colorbar.set_label("coefficiente di deflusso", fontsize=7)
    colorbar.ax.tick_params(labelsize=6)

    # La figura passa per un PNG in memoria invece di essere scritta e riletta:
    # matplotlib produce RGBA, e un TIFF con canale alfa non è quello che un
    # elaboratore di testi si aspetta di incollare. Rileggere il TIFF per
    # appiattirlo non è un'opzione — PIL lo mappa in memoria e su Windows il
    # file resta bloccato — quindi il disco si tocca una volta sola.
    buffer = io.BytesIO()
    figure.savefig(buffer, dpi=DPI, format="png", facecolor="white", bbox_inches="tight")
    pyplot.close(figure)
    buffer.seek(0)
    with Image.open(buffer) as drawn:
        drawn.convert("RGB").save(path, format="TIFF", dpi=(DPI, DPI), compression="tiff_lzw")
    return True
