"""Serializers del módulo de configuración de Frosty en el panel de Frostbyte.

Lo que el admin de Django resuelve con formularios, aquí lo consume la app:
mismos datos, mismas reglas, pero desde el celular —que es donde el dueño está
cuando se le ocurre cambiarle el tono al agente o mandarle un sticker nuevo.
"""

import re
from base64 import b64encode

from rest_framework import serializers

from .models import AgentSettings, Sticker
from .tones import TONE_PRESETS


class AgentSettingsSerializer(serializers.ModelSerializer):
    """Identidad, tono, dueño y los cuatro interruptores.

    El prompt base (reglas del pedido, cobertura, pagos) NO se expone: eso es
    lógica con tests detrás, no una preferencia editable desde una pantalla.

    El catálogo de tonos viaja con la configuración —nombre, para qué sirve y
    una frase de ejemplo— para que la pantalla no tenga que repetir esos textos
    por su cuenta: si aquí se afina un tono, allá se ve afinado.
    """

    tone_presets = serializers.SerializerMethodField()

    class Meta:
        model = AgentSettings
        fields = (
            "agent_name",
            "owner_phones",
            "tone_preset",
            "tone_presets",
            "tone",
            "stickers_enabled",
            "reactions_enabled",
            "product_photos_enabled",
            "quick_replies_enabled",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def get_tone_presets(self, obj):
        """Los tonos entre los que se puede elegir, sin el texto del prompt.

        Las instrucciones que van al modelo no salen de aquí a propósito: son
        prompt, no contenido de pantalla, y verlas a medias invita a editarlas
        donde no se pueden probar.
        """
        return [
            {
                "key": preset["key"],
                "name": preset["name"],
                "description": preset["description"],
                "sample": preset["sample"],
            }
            for preset in TONE_PRESETS
        ]

    def validate_agent_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("El agente necesita un nombre para presentarse.")
        return value

    def validate_owner_phones(self, value):
        """Deja la lista en dígitos separados por coma.

        Quien escribe desde el celular teclea "+57 316 427 7879" o pega el
        número con guiones; guardarlo así rompería `is_owner`, que compara
        dígitos. Se normaliza aquí en vez de exigirle el formato a quien edita.
        """
        numbers = []
        for part in (value or "").split(","):
            digits = re.sub(r"\D", "", part)
            if not digits:
                continue
            if len(digits) < 10:
                raise serializers.ValidationError(
                    f"«{part.strip()}» no parece un celular: escríbelo con indicativo "
                    "(ej. 573164277879)."
                )
            if digits not in numbers:
                numbers.append(digits)
        return ",".join(numbers)


class OptionalBooleanField(serializers.BooleanField):
    """Un booleano que ausente significa "déjalo como está", no "apágalo".

    DRF asume que todo formulario es HTML con checkboxes, donde un campo que no
    llega equivale a `False`. La subida de un sticker va en multipart porque
    lleva un archivo, así que sin esto crear uno sin tocar el interruptor lo
    dejaba desactivado: guardado, invisible para el agente y sin decir por qué.
    """

    def get_value(self, dictionary):
        if self.field_name not in dictionary:
            return serializers.empty
        return dictionary[self.field_name]


class StickerSerializer(serializers.ModelSerializer):
    """Un sticker del banco, con su miniatura lista para pintar.

    La miniatura viaja como data URI y no como enlace a propósito: la URL
    pública solo sirve los activos —justo los que no hay que revisar aquí— y en
    local apunta al backend de producción, donde este sticker no existe.
    """

    preview = serializers.SerializerMethodField()
    is_active = OptionalBooleanField(required=False)

    class Meta:
        model = Sticker
        fields = (
            "id",
            "label",
            "description",
            "is_active",
            "is_animated",
            "display_order",
            "byte_size",
            "sent_count",
            "preview",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "is_animated",
            "byte_size",
            "sent_count",
            "created_at",
            "updated_at",
        )

    def get_preview(self, obj):
        if not obj.data:
            return ""
        return f"data:image/webp;base64,{b64encode(bytes(obj.data)).decode()}"

    def validate_label(self, value):
        value = (value or "").strip().lower()
        if not value:
            raise serializers.ValidationError("Ponle un nombre corto, como lo pedirías tú.")
        return value

    def validate_description(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(
                "Escribe cuándo usarlo: es lo único que el agente lee para elegirlo."
            )
        return value
