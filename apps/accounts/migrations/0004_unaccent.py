from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Extensión ``unaccent`` de Postgres: la usa ``apps.search.Plain`` para
    que todas las búsquedas ignoren tildes."""

    dependencies = [
        ("accounts", "0003_user_email_opt_out"),
    ]

    operations = [
        UnaccentExtension(),
    ]
