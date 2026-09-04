"""Tests del catálogo: la búsqueda plana y los grupos de opciones apagados.

De la búsqueda se protege que a "Café Frío" lo encuentren "cafe", "CAFÉ",
"frio" y "cafe,frio", y a "Coca-Cola" la encuentren "coca cola" y "cocacola",
tanto desde la API (``?search=``) como desde el ORM (``__plain``) y el admin.

De los modificadores, que apagar un grupo lo retire de TODOS los canales: es
cómo se quita del menú una opción que ya no se ofrece sin borrar el histórico.
"""
from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.business.models import Business
from apps.products.models import (
    Category,
    ModifierGroup,
    ModifierOption,
    Product,
    ProductModifierGroup,
)
from apps.search import (
    matches_search,
    normalize_text,
    plain_search_field,
    search_tokens,
)


class NormalizacionTests(SimpleTestCase):
    def test_normalize_text_quita_tildes_mayusculas_y_puntuacion(self):
        self.assertEqual(normalize_text("Café Frío, Coca-Cola"), "cafe frio cocacola")
        self.assertEqual(normalize_text("  Ñame\n\tPiña  "), "name pina")
        self.assertEqual(normalize_text(None), "")

    def test_search_tokens_separa_por_puntuacion(self):
        self.assertEqual(search_tokens("Coca-Cola, café"), ["coca", "cola", "cafe"])
        self.assertEqual(search_tokens(" , "), [])

    def test_matches_search(self):
        self.assertTrue(matches_search("CAFÉ frio", "Café Frío", "con leche"))
        self.assertTrue(matches_search("leche,cafe", "Café Frío", "con leche"))
        self.assertTrue(matches_search("cocacola", "Coca-Cola"))
        self.assertTrue(matches_search("", "lo que sea"))
        self.assertFalse(matches_search("cafe te", "Café Frío", None))

    def test_plain_search_field(self):
        casos = {
            "name": "name__plain",
            "category__name": "category__name__plain",
            "^name": "^name__plain",
            "=slug": "=slug__plain",
            "id": "id",
            "business": "business",
            "name__icontains": "name__icontains",
            "@name": "@name",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(plain_search_field(Product, entrada), esperado)


class BusquedaProductosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        negocio = Business.objects.create(name="Local de prueba", slug="local-prueba")
        cocteles = Category.objects.create(business=negocio, name="Cócteles", slug="cocteles")
        bebidas = Category.objects.create(business=negocio, name="Bebidas", slug="bebidas")

        def producto(nombre, slug, categoria, descripcion):
            return Product.objects.create(
                business=negocio, category=categoria, name=nombre, slug=slug,
                description=descripcion,
            )

        producto("Café Frío", "cafe-frio", cocteles, "Con leche y hielo")
        producto("Coca-Cola", "coca-cola", bebidas, "Gaseosa")
        producto("Mango Biche", "mango-biche", cocteles, "Granizado clásico")

    def buscar(self, q):
        resp = APIClient().get("/api/v1/products/", {"search": q})
        self.assertEqual(resp.status_code, 200, resp.content)
        return sorted(p["name"] for p in resp.json()["results"])

    def test_api_ignora_mayusculas_y_tildes(self):
        for q in ("cafe", "CAFÉ", "Cafè", "frio", "FRÍO"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["Café Frío"])

    def test_api_ignora_puntuacion_y_orden(self):
        for q in ("cafe,frio", "frio cafe", "café-frío"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["Café Frío"])
        for q in ("coca cola", "cocacola", "coca-cola", "COCA"):
            with self.subTest(q=q):
                self.assertEqual(self.buscar(q), ["Coca-Cola"])

    def test_api_busca_en_descripcion(self):
        self.assertEqual(self.buscar("clasico"), ["Mango Biche"])

    def test_api_sin_termino_o_sin_coincidencia(self):
        self.assertEqual(len(self.buscar("")), 3)
        self.assertEqual(len(self.buscar(" , ")), 3)
        self.assertEqual(self.buscar("xyz"), [])

    def test_lookup_plain_en_el_orm(self):
        self.assertEqual(Product.objects.filter(name__plain__contains="cafe frio").count(), 1)
        self.assertEqual(Product.objects.filter(name__plain__startswith="COCA").count(), 1)
        self.assertEqual(
            Product.objects.filter(category__name__plain__contains="cocteles").count(), 2
        )

    def test_admin_busca_en_plano(self):
        model_admin = admin.site._registry[Product]
        request = RequestFactory().get("/admin/")
        qs, _ = model_admin.get_search_results(request, Product.objects.all(), "CAFE, frío")
        self.assertEqual([p.name for p in qs], ["Café Frío"])


class GrupoDeOpcionesApagadoTests(TestCase):
    """Un grupo apagado no existe para nadie: ni la carta ni el agente lo ven.

    El filtro miraba solo la asociación producto↔grupo, así que apagar el
    grupo lo quitaba del agente de WhatsApp pero lo dejaba en la carta: el
    cliente seguía eligiendo salsas que la cocina ya no tenía.
    """

    def setUp(self):
        negocio = Business.objects.create(name="Comida", slug="comida")
        categoria = Category.objects.create(business=negocio, name="Salchipapas", slug="salchi")
        self.producto = Product.objects.create(
            business=negocio, category=categoria, name="Salchipapa", slug="salchipapa"
        )
        self.grupo = ModifierGroup.objects.create(
            business=negocio, name="Salsas", min_select=0, max_select=3
        )
        ModifierOption.objects.create(group=self.grupo, name="Rosada")
        ProductModifierGroup.objects.create(product=self.producto, group=self.grupo)

    def detalle(self):
        resp = APIClient().get("/api/v1/products/salchipapa/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def listado(self):
        resp = APIClient().get("/api/v1/products/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["results"][0]

    def test_el_grupo_activo_sale_en_la_carta(self):
        self.assertEqual([g["name"] for g in self.detalle()["modifier_groups"]], ["Salsas"])
        self.assertTrue(self.listado()["is_configurable"])

    def test_apagar_el_grupo_lo_quita_de_la_carta(self):
        self.grupo.is_active = False
        self.grupo.save(update_fields=["is_active"])
        self.assertEqual(self.detalle()["modifier_groups"], [])
        self.assertFalse(self.listado()["is_configurable"])
