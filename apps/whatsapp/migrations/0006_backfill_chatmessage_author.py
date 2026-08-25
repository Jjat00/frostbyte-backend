"""Reparte el autor de los mensajes que ya estaban archivados.

`author` nació con default "cliente", así que los mensajes anteriores a la
migración 0005 quedaron todos como del cliente, incluidos los salientes. El
criterio para repartirlos es el mismo que usa el worker en vivo: un saliente
cuyo wamid registramos al enviarlo (SentMessage) lo mandó el agente; el que no
está registrado lo escribió una persona del equipo desde la app de WhatsApp.
"""

from django.db import migrations


def repartir_autores(apps, schema_editor):
    ChatMessage = apps.get_model("whatsapp", "ChatMessage")
    SentMessage = apps.get_model("whatsapp", "SentMessage")

    salientes = ChatMessage.objects.filter(direction="outbound")
    propios = set(
        SentMessage.objects.filter(
            wamid__in=salientes.values_list("wamid", flat=True)
        ).values_list("wamid", flat=True)
    )
    salientes.filter(wamid__in=propios).update(author="agent")
    salientes.exclude(wamid__in=propios).update(author="human")
    ChatMessage.objects.filter(direction="inbound").update(author="customer")


def volver_al_default(apps, schema_editor):
    apps.get_model("whatsapp", "ChatMessage").objects.update(author="customer")


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0005_alter_chatmessage_options_chatmessage_author_and_more"),
    ]

    operations = [
        migrations.RunPython(repartir_autores, volver_al_default),
    ]
