# Bonsai Salad — invarianza_idraulica tool

"""Il coefficiente di deflusso di un elemento e da dove viene.

Tre portatori possibili — istanza, tipo, materiale — con la precedenza IFC
consueta: il più specifico vince. Chi non dichiara nulla è trattato come
superficie impermeabile. L'assenza è un valore, non un'incognita.
"""

from collections import namedtuple

import ifcopenshell.api.pset
import ifcopenshell.util.element

PSET_NAME = "Invarianza idraulica"
COEFFICIENT = "Coefficiente di deflusso"

# I valori convenzionali della DGR 2948/2009, Allegato A: 0,1 aree agricole,
# 0,2 superfici permeabili, 0,6 semi-permeabili, 0,9 impermeabili (tetti,
# terrazze, strade, piazzali). Chi non dichiara nulla prende l'ultimo.
AGRICULTURAL = 0.1
PERMEABLE = 0.2
SEMI_PERMEABLE = 0.6
IMPERMEABLE = 0.9

DEFAULT = IMPERMEABLE

INSTANCE = "istanza"
TYPE = "tipo"
MATERIAL = "materiale"
PREDEFINED = "predefinito"
AMBIGUOUS = "ambiguo"

NO_MATERIAL = "(senza materiale)"

Coefficient = namedtuple("Coefficient", ("value", "source", "label"))


def _declared(definition, inherit=False):
    """Il valore nel pset di questa entità, None se assente o non numerico.
    I bool passerebbero per numeri: un True varrebbe 1,0 senza dirlo."""
    pset = ifcopenshell.util.element.get_psets(definition, should_inherit=inherit).get(PSET_NAME) or {}
    value = pset.get(COEFFICIENT)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def materials(element):
    """I materiali dell'elemento, qualunque forma abbia l'assegnazione."""
    return ifcopenshell.util.element.get_materials(element) or []


def _label(names):
    return " + ".join(names) if names else NO_MATERIAL


def coefficient(element):
    """Il coefficiente da applicare all'elemento, con la sua provenienza.

    L'etichetta nomina i materiali che lo hanno dichiarato quando sono loro a
    deciderlo, altrimenti tutti quelli dell'elemento: è ciò che intesta la riga
    di tabella, e deve dire di quale superficie si sta parlando."""
    every = _label([material.Name for material in materials(element) if material.Name])

    value = _declared(element)
    if value is not None:
        return Coefficient(value, INSTANCE, every)

    element_type = ifcopenshell.util.element.get_type(element)
    if element_type is not None:
        value = _declared(element_type)
        if value is not None:
            return Coefficient(value, TYPE, every)

    declared = {}
    for material in materials(element):
        value = _declared(material)
        if value is not None:
            declared[material.Name or NO_MATERIAL] = value

    values = set(declared.values())
    if len(values) > 1:
        return Coefficient(DEFAULT, AMBIGUOUS, every)
    if len(values) == 1:
        return Coefficient(values.pop(), MATERIAL, _label(sorted(declared)))
    return Coefficient(DEFAULT, PREDEFINED, every)


def _pset_entity(ifc_file, definition):
    existing = ifcopenshell.util.element.get_pset(definition, PSET_NAME, should_inherit=False)
    if existing:
        return ifc_file.by_id(existing["id"])
    return ifcopenshell.api.pset.add_pset(ifc_file, product=definition, name=PSET_NAME)


def declare(ifc_file, definition, value):
    """Scrive il coefficiente su un'entità qualunque dei tre livelli: elemento,
    tipo o materiale. Su un IfcMaterial il pset è un IfcMaterialProperties, che
    l'API costruisce da sé.

    Il property set viene riusato se c'è già: due omonimi sullo stesso portatore
    e il lettore ne troverebbe uno a caso."""
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=_pset_entity(ifc_file, definition),
        # Un coefficiente è un numero puro: non un conteggio, non una misura.
        properties={COEFFICIENT: ifc_file.create_entity("IfcReal", float(value))},
    )
