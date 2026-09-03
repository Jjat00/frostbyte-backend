"""Serializers del módulo de configuración de Frosty en el panel de Frostbyte.

Lo que el admin de Django resuelve con formularios, aquí lo consume la app:
mismos datos, mismas reglas, pero desde el celular —que es donde el dueño está
cuando se le ocurre cambiarle el tono al agente o mandarle un sticker nuevo.
"""

import re
from base64 import b64encode

from rest_framework import serializers

from .models import AgentSettings, AgentTone, Sticker


class AgentToneSerializer(serializers.ModelSerializer):
    """Una personalidad del catálogo, tal como se edita desde el panel.

    A diferencia del resto de la configuración, aquí SÍ viaja texto de prompt:
    `persona` es el bloque QUIÉN ERES que lee el modelo, y es justo lo que el
    dueño vino a cambiar. La clave no se edita —es lo que apunta desde la
    configuración— y se genera del nombre al crear el tono.
    """

    is_modified = serializers.BooleanField(read_only=True)

    class Meta:
        model = AgentTone
        fields = (
            "id",
            "key",
            "name",
            "description",
            "sample",
            "persona",
            "is_builtin",
            "is_modified",
            "display_order",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("key", "is_builtin", "created_at", "updated_at")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Ponle un nombre: es lo que verás al elegirlo.")
        return value

    def validate_description(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Resume el tono en una línea, para reconocerlo.")
        return value

    def validate_persona(self, value):
        """Un tono corto no es un tono: es una instrucción suelta.

        El texto entra al prompt reemplazando la personalidad entera, así que
        dejarlo en dos palabras deja al agente sin nada que lo sostenga.
        """
        value = (value or "").strip()
        if len(value) < 40:
            raise serializers.ValidationError(
                "Descríbela de verdad: cómo trata al cliente, cómo habla y qué hace "
                "cuando alguien está molesto."
            )
        return value

    def create(self, validated_data):
        validated_data["key"] = AgentTone.make_key(validated_data.get("name"))
        if not validated_data.get("display_order"):
            ultimo = AgentTone.objects.order_by("-display_order").first()
            validated_data["display_order"] = (ultimo.display_order if ultimo else 0) + 1
        return super().create(validated_data)


class AgentSettingsSerializer(serializers.ModelSerializer):
    """Identidad, tono, dueño y los cuatro interruptores.

    El prompt base (reglas del pedido, cobertura, pagos) NO se expone: eso es
    lógica con tests detrás, no una preferencia editable desde una pantalla.

    El catálogo de tonos viaja con la configuración para que la pantalla lo
    pinte sin una segunda consulta; editarlos, en cambio, va por su propio
    endpoint (`/whatsapp/agent-tones/`).
    """

    tone_presets = AgentToneSerializer(source="tone_catalog", many=True, read_only=True)

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

    def validate_tone_preset(self, value):
        """El tono elegido tiene que existir todavía.

        Se comprueba contra la tabla y no contra una lista fija: el catálogo
        cambia desde el panel, y aceptar una clave muerta dejaría al agente
        hablando con la personalidad de otro sin avisar.
        """
        if not AgentTone.objects.filter(key=value).exists():
            raise serializers.ValidationError("Ese tono ya no existe; elige uno del catálogo.")
        return value

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
