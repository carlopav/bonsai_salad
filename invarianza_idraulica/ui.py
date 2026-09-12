# Bonsai Salad — invarianza_idraulica tool

import os

import bpy
from bonsai import tool

from .core import coefficients
from .data import Summary
from .operator import (
    INSTANCE_TARGET,
    MATERIAL_TARGET,
    TYPE_TARGET,
    output_dir,
    targets,
    zone_items,
)

TARGET_LABELS = {
    MATERIAL_TARGET: "Scrivi sul materiale attivo",
    TYPE_TARGET: "Scrivi sul tipo dei selezionati",
    INSTANCE_TARGET: "Scrivi sui selezionati",
}


class InvarianzaIdraulicaProperties(bpy.types.PropertyGroup):
    coefficient: bpy.props.FloatProperty(
        name="Coefficiente di deflusso",
        description=(
            "Quanta pioggia la superficie lascia defluire invece di trattenerla. "
            "Valori convenzionali della DGR Veneto 2948/2009, Allegato A: "
            "0,1 aree agricole; 0,2 superfici permeabili (aree verdi); "
            "0,6 semi-permeabili (grigliati drenanti su materasso ghiaioso, terra battuta, "
            "stabilizzato); 0,9 impermeabili (tetti, terrazze, strade, piazzali)"
        ),
        default=coefficients.IMPERMEABLE,
        soft_min=0.0,
        soft_max=1.0,
    )
    target: bpy.props.EnumProperty(
        name="Scrivi su",
        description="Chi porta il coefficiente: il materiale lo dà a tutti i suoi elementi, il tipo a tutte le sue istanze, l'istanza solo a sé",
        items=[
            (MATERIAL_TARGET, "Materiale attivo", "Il materiale selezionato nella scheda Materials di Bonsai"),
            (TYPE_TARGET, "Tipo", "I tipi degli oggetti selezionati"),
            (INSTANCE_TARGET, "Istanza", "Gli oggetti selezionati, uno per uno"),
        ],
        default=MATERIAL_TARGET,
    )
    zone: bpy.props.EnumProperty(
        name="Ambito",
        description="La zona spaziale che delimita l'ambito di intervento",
        items=zone_items,
    )


class InvarianzaIdraulicaPanel(bpy.types.Panel):
    bl_label = "Invarianza idraulica"
    bl_idname = "BONSAI_SALAD_PT_invarianza"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        pass


class InvarianzaIdraulicaSurfacesPanel(bpy.types.Panel):
    bl_label = "Verifica delle superfici"
    bl_idname = "BONSAI_SALAD_PT_invarianza_surfaces"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_invarianza"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        if tool.Ifc.get() is None:
            layout.label(text="Nessun file IFC caricato.", icon="ERROR")
            return
        if not zone_items(None, context):
            layout.label(text="Nessuna IfcSpatialZone nel file.", icon="ERROR")
            return

        props = context.scene.invarianza_idraulica
        self.draw_coefficient(context, layout, props)

        layout.separator()
        layout.prop(props, "zone")
        layout.operator("bim.salad_measure_invarianza_surfaces", icon="DRIVER")
        self.draw_summary(layout)

        layout.operator("bim.salad_export_invarianza_surfaces", icon="EXPORT")
        if tool.Ifc.get_path():
            project = os.path.dirname(tool.Ifc.get_path())
            layout.label(text=os.path.relpath(output_dir(), project), icon="FILE_FOLDER")

    def draw_coefficient(self, context, layout, props):
        column = layout.column(align=True)
        column.prop(props, "coefficient")
        column.prop(props, "target")
        # Il pulsante è spento quando il bersaglio non c'è: premerlo a vuoto e
        # leggere l'errore dopo è peggio che vederlo grigio prima.
        found = targets(context, props.target)
        write = column.row(align=True)
        write.enabled = bool(found)
        operator = write.operator(
            "bim.salad_set_deflusso_coefficient", text=TARGET_LABELS[props.target], icon="GREASEPENCIL"
        )
        operator.coefficient, operator.target = props.coefficient, props.target
        if not found and props.target == MATERIAL_TARGET:
            # Il browser materiali di Bonsai mostra anche categorie e insiemi
            # stratigrafici: da lì non si può scrivere, e il pulsante spento da
            # solo non spiegherebbe perché.
            column.label(text="Scegli un IfcMaterial nella scheda Materials.", icon="INFO")
        self.draw_active_element(context, column)

    def draw_active_element(self, context, layout):
        """A quanto risolve adesso l'oggetto attivo: senza questo la scrittura
        è muta, e non si distingue un coefficiente ereditato da uno assente."""
        element = tool.Ifc.get_entity(context.active_object) if context.active_object else None
        if element is None:
            return
        found = coefficients.coefficient(element)
        name = element.Name or element.is_a()
        if found.source == coefficients.AMBIGUOUS:
            layout.label(text=f"{name}: materiali discordi, vale {found.value:.2f}", icon="LIBRARY_DATA_BROKEN")
            return
        layout.label(text=f"{name}: φ {found.value:.2f} — da {found.source}", icon="INFO")

    def draw_summary(self, layout):
        summary = Summary.load()
        measurement = summary.get("measurement")
        if measurement is None:
            if summary.get("dropped"):
                layout.label(text="Riepilogo scaduto: ricalcola.", icon="FILE_REFRESH")
            else:
                layout.label(text="Non ancora calcolato.", icon="INFO")
            return

        box = layout.box()
        box.label(text=f"Superficie complessiva: {measurement.total:.2f} m²", icon="MESH_PLANE")
        table = box.column(align=True)
        for row in measurement.rows:
            table.label(
                text=f"{row.label} — φ {row.coefficient:.2f} — "
                f"{row.area:.2f} m² → {row.area * row.coefficient:.2f} m²"
            )
        box.label(text=f"Superficie impermeabile equivalente: {measurement.impermeable:.2f} m²")
        box.label(text=f"φ medio: {measurement.mean_coefficient:.3f}")

        if measurement.ambiguous:
            box.label(text=f"{len(measurement.ambiguous)} elementi con materiali discordi", icon="LIBRARY_DATA_BROKEN")
        if measurement.unmeasurable:
            box.label(text=f"{len(measurement.unmeasurable)} corpi non misurabili", icon="QUESTION")


classes = (InvarianzaIdraulicaProperties, InvarianzaIdraulicaPanel, InvarianzaIdraulicaSurfacesPanel)
