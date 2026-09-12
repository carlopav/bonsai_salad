# Bonsai Salad — invarianza_idraulica tool

import os

import bpy
from bonsai import tool

import ifcopenshell.util.element
import ifcopenshell.util.geolocation

from .core import coefficients, ods, plan, surfaces
from .data import Summary

AMBITO = "ambito di intervento"
OUTPUT_DIR = "invarianza-idraulica"

MATERIAL_TARGET, TYPE_TARGET, INSTANCE_TARGET = "MATERIAL", "TYPE", "INSTANCE"


def active_material():
    """Il materiale scelto nel browser materiali di Bonsai, None se lì non c'è
    una riga o se la riga è una categoria. Si punta quello, non un materiale
    dedotto dalla selezione: un pacchetto stratigrafico ne ha più d'uno, e
    indovinare quale intendeva l'utente è il modo di scrivere sul posto
    sbagliato."""
    props = tool.Material.get_material_props()
    item = props.active_material
    if item is None or getattr(item, "is_category", False) or not item.ifc_definition_id:
        return None
    material = tool.Ifc.get().by_id(item.ifc_definition_id)
    return material if material.is_a("IfcMaterial") else None


def selected_elements(context):
    elements = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [element for element in elements if element is not None]


def selected_types(context):
    """I tipi degli oggetti selezionati, una volta ciascuno."""
    found = {}
    for element in selected_elements(context):
        element_type = ifcopenshell.util.element.get_type(element)
        if element_type is not None:
            found[element_type.id()] = element_type
    return list(found.values())


def targets(context, target):
    """Le entità su cui il pulsante scriverebbe, per il bersaglio scelto."""
    if target == MATERIAL_TARGET:
        material = active_material()
        return [material] if material is not None else []
    if target == TYPE_TARGET:
        return selected_types(context)
    return selected_elements(context)


def zones(ifc_file):
    """Le zone spaziali del file, quelle che si chiamano "ambito di intervento"
    per prime: la prima voce di un EnumProperty è quella selezionata all'avvio,
    e l'ordine è il solo modo di proporre un default senza imporlo. Vuoto su
    IFC2X3, che non ha questa classe."""
    try:
        found = list(ifc_file.by_type(surfaces.ZONE_CLASS))
    except RuntimeError:
        return []

    def is_ambito(zone):
        names = (zone.Name or "", zone.ObjectType or "")
        return any(" ".join(name.lower().split()) == AMBITO for name in names)

    return sorted(found, key=lambda zone: (not is_ambito(zone), (zone.Name or "").lower()))


def zone_items(self, context):
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return []
    return [(zone.GlobalId, zone.Name or zone.GlobalId, zone.ObjectType or "") for zone in zones(ifc_file)]


def active_zone(context):
    """La zona scelta nel dropdown, None se il file non ne ha o la scelta non
    esiste più."""
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return None
    chosen = context.scene.invarianza_idraulica.zone
    for zone in zones(ifc_file):
        if zone.GlobalId == chosen:
            return zone
    return None


def output_dir():
    """La cartella delle uscite accanto all'IFC. Le uscite di questo tool sono
    più d'una e si consegnano insieme: hanno cartella propria."""
    return os.path.join(os.path.dirname(tool.Ifc.get_path()), OUTPUT_DIR)


def _stem(zone):
    return tool.Drawing.sanitise_filename(zone.Name or "Ambito").strip() or "Ambito"


def table_path(zone):
    return os.path.join(output_dir(), f"Superfici scolanti - {_stem(zone)}.ods")


def plan_path(zone):
    return os.path.join(output_dir(), f"Superfici scolanti - {_stem(zone)}.tif")


def true_north(ifc_file):
    """L'angolo del nord vero in radianti, None se il contesto non lo porta."""
    try:
        return ifcopenshell.util.geolocation.get_true_north(ifc_file)
    except Exception:
        return None


class SetCoefficient(bpy.types.Operator, tool.Ifc.Operator):
    """Scrive il coefficiente di deflusso sul bersaglio scelto, come property
    set "Invarianza idraulica" """

    bl_idname = "bim.salad_set_deflusso_coefficient"
    bl_label = "Scrivi coefficiente"
    bl_options = {"REGISTER", "UNDO"}

    coefficient: bpy.props.FloatProperty(name="Coefficiente di deflusso")
    target: bpy.props.StringProperty(name="Bersaglio")

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        written = targets(context, self.target)
        if not written:
            self.report({"ERROR"}, "Nessun bersaglio: seleziona un materiale o degli oggetti.")
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        for definition in written:
            coefficients.declare(ifc_file, definition, self.coefficient)
        # I numeri calcolati prima di questa scrittura non valgono più: il
        # pannello deve dirlo, non mostrarli.
        Summary.invalidate()
        self.report({"INFO"}, f"φ {self.coefficient:.2f} scritto su {len(written)}.")
        return {"FINISHED"}


class MeasureSurfaces(bpy.types.Operator):
    """Misura le superfici scolanti dell'ambito scelto e ne ricava la
    superficie impermeabile equivalente"""

    bl_idname = "bim.salad_measure_invarianza_surfaces"
    bl_label = "Calcola superfici"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None and active_zone(context) is not None

    def execute(self, context):
        zone = active_zone(context)
        try:
            measurement = surfaces.measure(tool.Ifc.get(), zone)
        except surfaces.NoBoundary as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        Summary.store(measurement, zone)
        self.report({"INFO"}, f"{measurement.total:.2f} m², φ medio {measurement.mean_coefficient:.3f}")
        return {"FINISHED"}


class ExportSurfaces(bpy.types.Operator):
    """Scrive tabella e planimetria delle superfici nella cartella del progetto"""

    bl_idname = "bim.salad_export_invarianza_surfaces"
    bl_label = "Esporta"

    @classmethod
    def poll(cls, context):
        return Summary.load().get("measurement") is not None

    def execute(self, context):
        summary = Summary.load()
        measurement, zone = summary["measurement"], active_zone(context)
        # Il riepilogo è di un ambito preciso: esportarlo sotto il nome di un
        # altro consegnerebbe numeri veri intestati alla zona sbagliata.
        if zone is None or zone.GlobalId != summary["zone"]:
            self.report({"ERROR"}, "Il riepilogo non è di questo ambito: ricalcola.")
            return {"CANCELLED"}
        os.makedirs(output_dir(), exist_ok=True)
        title = zone.Name or "Ambito di intervento"
        ods.write(measurement, table_path(zone), title)
        drawn = plan.render(measurement, plan_path(zone), title, true_north(tool.Ifc.get()))
        if not drawn:
            self.report({"WARNING"}, "Tabella scritta; planimetria saltata: matplotlib non disponibile.")
            return {"FINISHED"}
        self.report({"INFO"}, "Tabella e planimetria scritte.")
        return {"FINISHED"}


classes = (SetCoefficient, MeasureSurfaces, ExportSurfaces)
