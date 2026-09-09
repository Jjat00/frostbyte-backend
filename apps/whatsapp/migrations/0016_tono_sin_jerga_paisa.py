"""El tono de fábrica deja de decir «parce».

No se dice en todo el país: en Cumbal marca a un forastero, que es justo lo
contrario de lo que busca este tono. En producción Jaime ya lo había reescrito
a mano; esto pone al día las bases donde el tono siga tal como salió de
fábrica. Una fila editada no se toca: ahí manda quien la escribió.
"""

from django.db import migrations

VIEJO_NAME = "Parcero"
VIEJO_SAMPLE = "Qué más parce, ¿lo de siempre o hoy probamos algo nuevo?"
VIEJA_DESCRIPCION = "Caluroso, chistoso y rápido, hablando como en Nariño. El de siempre."
VIEJA_PERSONA = (
    "QUIÉN ERES: un parcero del pueblo atendiendo su local, no un formulario. "
    "Caluroso, chistoso y rápido. Tuteas siempre, hablas como se habla en Nariño "
    '("parce", "de una", "listo pues", "hágale", "qué más", "bacano") sin exagerar '
    "el acento ni sonar a caricatura. El chiste va DENTRO de la frase que ya ibas a "
    "decir, nunca en un mensaje aparte ni alargándola: eres el amigo que contesta "
    "corto y con chispa, no el que hace show. Si el cliente está molesto, tiene un "
    "problema o está reclamando, se acabó el chiste: ahí eres puro respeto y solución."
)


def quitar_la_jerga_paisa(apps, schema_editor):
    from apps.whatsapp.tones import seed_tone

    nuevo = seed_tone("parcero")
    AgentTone = apps.get_model("whatsapp", "AgentTone")
    tono = AgentTone.objects.filter(key="parcero").first()
    if tono is None:
        return
    # Campo por campo: el dueño pudo reescribir la personalidad y dejar el
    # nombre, o al revés. Lo que siga siendo de fábrica se actualiza; lo suyo
    # se queda como lo escribió, aunque diga «parce».
    cambios = []
    for campo, viejo in (
        ("name", VIEJO_NAME),
        ("description", VIEJA_DESCRIPCION),
        ("sample", VIEJO_SAMPLE),
        ("persona", VIEJA_PERSONA),
    ):
        if getattr(tono, campo) == viejo:
            setattr(tono, campo, nuevo[campo])
            cambios.append(campo)
    if cambios:
        tono.save(update_fields=cambios)


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0015_agentsettings_banned_words"),
    ]

    operations = [
        migrations.RunPython(quitar_la_jerga_paisa, migrations.RunPython.noop),
    ]
