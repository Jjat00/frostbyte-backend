"""Búsqueda insensible a mayúsculas, tildes y puntuación en toda la plataforma.

Una sola regla para todos los buscadores (API, admin y filtros en Python):
se compara texto "plano", es decir, sin tildes, en minúsculas y sin signos de
puntuación. Así a "Café Frío" lo encuentran "cafe", "CAFÉ", "frio" o
"cafe,frio", y a "Coca-Cola" la encuentran "coca cola" y "cocacola".

- ``normalize_text`` / ``search_tokens`` / ``matches_search``: la regla en Python.
- ``Plain``: la misma regla como transform del ORM (``name__plain__contains``).
  Usa ``unaccent`` de Postgres, extensión creada en la migración
  ``accounts.0004_unaccent``. Es bilateral: el valor buscado pasa por la
  misma limpieza que la columna.
- ``PlainSearchFilter``: el ``SearchFilter`` de DRF con esa regla; se usa
  igual, con los mismos ``search_fields``.
- ``PlainSearchAdminMixin``: lo mismo para los ``search_fields`` del admin.

El front aplica la misma regla en ``src/lib/search.js``.
"""
import re
import unicodedata

from django.core.exceptions import FieldDoesNotExist
from django.db.models import CharField, TextField, Transform
from django.db.models.constants import LOOKUP_SEP
from rest_framework import fields as drf_fields
from rest_framework.filters import SearchFilter, search_smart_split

# Tras quitar tildes y pasar a minúsculas, esto es lo único que sobrevive.
_NOT_PLAIN = re.compile(r"[^a-z0-9\s]+")
_SPACES = re.compile(r"\s+")
# En lo que escribe el usuario, la puntuación separa palabras; las comillas
# se conservan para que ``"frase exacta"`` siga funcionando.
_QUERY_PUNCT = re.compile(r"[^\w\s\"']+")


def strip_accents(text):
    """'Clásica' -> 'Clasica'. Acepta None."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(text):
    """Texto plano de un campo: sin tildes, minúsculas, sin puntuación.

    >>> normalize_text("Café Frío, Coca-Cola")
    'cafe frio cocacola'
    """
    plain = _NOT_PLAIN.sub("", strip_accents(text).lower())
    return _SPACES.sub(" ", plain).strip()


def search_tokens(query):
    """Palabras de lo que escribió el usuario; la puntuación separa palabras.

    >>> search_tokens("Coca-Cola, café")
    ['coca', 'cola', 'cafe']
    """
    return _NOT_PLAIN.sub(" ", strip_accents(query).lower()).split()


def matches_search(query, *fields):
    """True si cada palabra de ``query`` aparece en alguno de los ``fields``."""
    tokens = search_tokens(query)
    if not tokens:
        return True
    haystack = " ".join(normalize_text(f) for f in fields)
    return all(token in haystack for token in tokens)


class Plain(Transform):
    """``campo__plain``: la regla de ``normalize_text`` en SQL.

    Bilateral: ``name__plain__contains="Café"`` limpia también el "Café".
    """

    lookup_name = "plain"
    bilateral = True

    def as_sql(self, compiler, connection):
        lhs, params = compiler.compile(self.lhs)
        sql = (
            "regexp_replace(regexp_replace(lower(unaccent(%s::text)), "
            "'[^a-z0-9\\s]', '', 'g'), '\\s+', ' ', 'g')"
        ) % lhs
        return sql, params


CharField.register_lookup(Plain)
TextField.register_lookup(Plain)

_PREFIXES = "^=@$"


def plain_search_field(model, field_name):
    """``"name"`` -> ``"name__plain"`` si apunta a un campo de texto.

    Sigue relaciones (``category__name``), respeta los prefijos de DRF/admin
    (``^name``) y deja intacto lo que no sea texto (``id``) o ya traiga un
    lookup explícito.
    """
    prefix = field_name[0] if field_name[:1] in _PREFIXES else ""
    if prefix in ("@", "$"):
        return field_name
    opts = model._meta
    field = None
    for part in field_name[len(prefix):].split(LOOKUP_SEP):
        if part == "pk":
            part = opts.pk.name
        try:
            field = opts.get_field(part)
        except FieldDoesNotExist:
            return field_name
        if hasattr(field, "path_infos"):
            opts = field.path_infos[-1].to_opts
    if isinstance(field, (CharField, TextField)) and not field.is_relation:
        return field_name + LOOKUP_SEP + "plain"
    return field_name


class PlainSearchFilter(SearchFilter):
    """``?search=`` de DRF sin distinguir mayúsculas, tildes ni puntuación."""

    def get_search_terms(self, request):
        raw = request.query_params.get(self.search_param, "")
        raw = drf_fields.CharField(
            trim_whitespace=False, allow_blank=True
        ).run_validation(raw)
        raw = _QUERY_PUNCT.sub(" ", strip_accents(raw).lower())
        terms = (normalize_text(term) for term in search_smart_split(raw))
        return [term for term in terms if term]

    def construct_search(self, field_name, queryset):
        prefix = field_name[0] if field_name[:1] in self.lookup_prefixes else ""
        plain = plain_search_field(queryset.model, field_name)
        if not plain.endswith(LOOKUP_SEP + "plain"):
            return super().construct_search(field_name, queryset)
        lookup = {"^": "startswith", "=": "exact"}.get(prefix, "contains")
        return LOOKUP_SEP.join([plain[len(prefix):], lookup])


class PlainSearchAdminMixin:
    """Para ``ModelAdmin``: los ``search_fields`` de texto buscan en plano."""

    def get_search_fields(self, request):
        return [
            plain_search_field(self.model, f)
            for f in super().get_search_fields(request)
        ]

    def get_search_results(self, request, queryset, search_term):
        search_term = _QUERY_PUNCT.sub(" ", search_term)
        return super().get_search_results(request, queryset, search_term)
