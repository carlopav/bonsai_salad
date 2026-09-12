# Bonsai Salad — invarianza_idraulica tool

"""L'impronta in pianta di un corpo IFC: i suoi triangoli proiettati su XY e
uniti, più la quota del suo punto più alto.

Si misura in proiezione perché è la proiezione che raccoglie la pioggia: una
falda inclinata ne intercetta quanto la sua ombra a mezzogiorno, non quanto la
sua superficie reale. È anche la sola misura che somma all'area del lotto senza
contare due volte lo stesso punto.

create_shape restituisce metri SI qualunque sia l'unità di progetto: le aree
sono già m² e non vanno riconvertite.
"""

from collections import namedtuple

import numpy as np
import shapely

import ifcopenshell.geom
import ifcopenshell.util.representation
import ifcopenshell.util.shape

# Un triangolo verticale proietta a un segmento: la sua area di proiezione è
# nulla a meno degli errori di macchina, e va scartato prima di farne un poligono.
TOLERANCE = 1e-9

Footprint = namedtuple("Footprint", ("polygon", "top"))


def settings():
    geom_settings = ifcopenshell.geom.settings()
    # L'impronta è una proiezione lungo lo Z del mondo: un elemento posato con
    # una rotazione va misurato dopo di essa, non nel proprio riferimento.
    geom_settings.set("use-world-coords", True)
    return geom_settings


def has_body(element):
    """Se l'elemento porta una rappresentazione Body. Distingue chi non ha
    corpo — e non va segnalato — da chi ce l'ha e non si è lasciato costruire."""
    return ifcopenshell.util.representation.get_representation(element, "Model", "Body") is not None


def _projected_areas(triangles):
    """Area con segno di ogni triangolo proiettato, con la formula di Gauss."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))


def footprint(element, geom_settings=None):
    """L'impronta in pianta del corpo, in m², e la sua quota massima. None per
    un elemento senza corpo o il cui corpo non si lascia costruire."""
    try:
        # La shape deve sopravvivere alla propria geometry: letta da un
        # temporaneo, i buffer dei vertici tornano vuoti.
        shape = ifcopenshell.geom.create_shape(geom_settings or settings(), element)
    except RuntimeError:
        return None
    geometry = shape.geometry
    faces = ifcopenshell.util.shape.get_faces(geometry)
    if not len(faces):
        return None
    vertices = ifcopenshell.util.shape.get_vertices(geometry)
    flat = vertices[faces][:, :, :2]
    standing = flat[np.abs(_projected_areas(flat)) > TOLERANCE]
    if not len(standing):
        return None
    polygon = shapely.union_all([shapely.Polygon(triangle) for triangle in standing])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        return None
    return Footprint(polygon, float(vertices[:, 2].max()))
