# Bonsai Salad — invarianza_idraulica tool

from bonsai import tool


class Summary:
    """L'ultimo calcolo, tenuto perché il pannello possa mostrarlo senza
    rifarlo a ogni ridisegno: costruire le geometrie di un intero lotto in
    draw() è fuori questione."""

    is_loaded = False
    data = {}

    @classmethod
    def refresh(cls):
        cls.is_loaded = False

    @classmethod
    def load(cls):
        if not cls.is_loaded or cls.is_stale():
            # Un riepilogo perso per un cambio di file non è un riepilogo mai
            # calcolato: il pannello non deve dire che non si è mai calcolato.
            dropped = bool(cls.data.get("measurement")) and not cls.is_stale()
            cls.data = {"path": tool.Ifc.get_path(), "measurement": None, "zone": None, "dropped": dropped}
            cls.is_loaded = True
        return cls.data

    @classmethod
    def is_stale(cls):
        """Bonsai svuota le proprie cache a ogni modifica dell'IFC, e un modulo
        che non è suo non è in quella lista: senza questo il pannello
        risponderebbe per il file aperto prima."""
        return cls.data.get("path") != tool.Ifc.get_path()

    @classmethod
    def invalidate(cls):
        """Butta la misura tenendo il file: dopo una scrittura sui coefficienti
        i numeri di prima sono sbagliati, e vanno tolti di mezzo, non aggiornati
        di nascosto."""
        if cls.data.get("measurement") is not None:
            cls.data = {"path": cls.data.get("path"), "measurement": None, "zone": None, "dropped": True}

    @classmethod
    def store(cls, measurement, zone):
        cls.data = {
            "path": tool.Ifc.get_path(),
            "measurement": measurement,
            "zone": zone.GlobalId,
            "dropped": False,
        }
        cls.is_loaded = True
