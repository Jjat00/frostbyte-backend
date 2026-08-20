"""Reclasifica como inversion los gastos que compraron algo que dura.

Nace de un problema real de Frostbyte: los ocho primeros meses tienen el
montaje de un piso, dos televisores, una nevera y las sillas registrados como
gasto corriente, y eso hace ver en perdida un mes que cerro con 36% de margen.
El mismo problema lo va a tener cualquier local que llegue con historia ya
cargada, asi que el comando se escribe generico: reglas por palabra clave,
umbral de materialidad y revision antes de escribir.

Por defecto solo muestra lo que haria. Escribe unicamente con --apply.
"""

import re
import unicodedata
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from apps.expenses.models import ExpenseCategory, ExpenseKind, OperationalExpense


def normalizar(texto):
    """Minusculas sin tildes, para que 'Adecuación' y 'adecuacion' sean lo mismo."""
    texto = unicodedata.normalize('NFD', texto or '')
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    return texto.lower()


# Palabras que, aunque aparezcan junto a un activo, delatan gasto corriente:
# "repuestos de maquina" o "arreglo de la nevera" no compran nada nuevo.
EXCLUSIONES = [
    'repuesto', 'arreglo', 'reparacion', 'mantenimiento', 'servicio tecnico',
    'alquiler', 'arriendo', 'recarga', 'lavado', 'camara de comercio',
]

# Cada regla lleva la categoria destino y las palabras que la disparan.
# Se comparan con limite de palabra, para que 'obra' no enganche 'maniobra'.
REGLAS = [
    ('remodeling', [
        'implementacion', 'adecuacion', 'remodelacion', 'obra', 'contratista',
        'nuevo local', 'piso 3', 'tercer piso', 'montaje',
    ]),
    ('equipment', [
        'televisor', 'televisores', 'tv', 'nevera', 'congelador', 'estufa',
        'licuadora', 'horno', 'cafetera', 'celular', 'computador', 'portatil',
        'impresora', 'equipo de sonido', 'parlante', 'panasonic', 'aire acondicionado',
        'camara de seguridad', 'camara seguridad', 'router', 'tablet',
        'maquina de', 'olla a presion',
    ]),
    ('furniture', [
        'silla', 'sillas', 'mesa', 'mesas', 'barra', 'estanteria', 'vitrina',
        'mueble', 'muebles', 'sofa', 'poltrona',
    ]),
]


def compilar(palabras):
    return [re.compile(r'(?<!\w)' + re.escape(p) + r'(?!\w)') for p in palabras]


REGLAS_COMPILADAS = [(slug, compilar(palabras)) for slug, palabras in REGLAS]
EXCLUSIONES_COMPILADAS = compilar(EXCLUSIONES)


class Command(BaseCommand):
    help = (
        'Propone (y con --apply aplica) la reclasificacion de gastos que en '
        'realidad son inversion: equipos, mobiliario y montaje de local.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Escribe los cambios. Sin esta bandera solo muestra la propuesta.'
        )
        parser.add_argument(
            '--min-amount', type=int, default=200000,
            help='Umbral de materialidad en COP (default 200000). Por debajo se '
                 'deja como gasto corriente, salvo que la regla sea de montaje.'
        )
        parser.add_argument(
            '--business', type=str, default=None,
            help='Slug del negocio a procesar. Sin esto, todos.'
        )
        parser.add_argument(
            '--ids', type=str, default='',
            help='IDs separados por coma que se fuerzan a inversion, salte o no la regla.'
        )
        parser.add_argument(
            '--exclude-ids', type=str, default='',
            help='IDs separados por coma que se dejan como estan.'
        )
        parser.add_argument(
            '--keyword', action='append', default=[],
            help='Palabra propia de este local, como categoria:palabra '
                 '(ej: --keyword remodeling:constructora). Repetible. Sirve para el '
                 'nombre del contratista o proveedor de cada local, que las '
                 'reglas genericas no pueden conocer.'
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        minimo = Decimal(options['min_amount'])
        forzados = self._parse_ids(options['ids'])
        excluidos = self._parse_ids(options['exclude_ids'])
        self.reglas = self._construir_reglas(options['keyword'])

        categorias = {c.slug: c for c in ExpenseCategory.objects.all()}
        faltantes = [slug for slug, _ in REGLAS if slug not in categorias]
        if faltantes:
            raise CommandError(
                'Faltan categorias de inversion: ' + ', '.join(faltantes) +
                '. Corre primero: python manage.py load_expense_categories'
            )

        qs = OperationalExpense.objects.select_related('category', 'business').filter(
            kind=ExpenseKind.OPERATIONAL
        )
        if options['business']:
            qs = qs.filter(business__slug=options['business'])

        propuestas = []
        for gasto in qs.order_by('expense_date', 'id'):
            if gasto.id in excluidos:
                continue
            destino = self._clasificar(gasto, minimo, forzado=gasto.id in forzados)
            if destino:
                propuestas.append((gasto, categorias[destino]))

        if not propuestas:
            self.stdout.write(self.style.WARNING('No hay gastos que reclasificar.'))
            return

        self._imprimir(propuestas, minimo)

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nSimulacion. Nada se escribio. Revisa la lista y vuelve a correr '
                'con --apply (y --exclude-ids para lo que no aplique).'
            ))
            return

        with transaction.atomic():
            for gasto, categoria in propuestas:
                gasto.kind = ExpenseKind.INVESTMENT
                gasto.category = categoria
                gasto.save(update_fields=['kind', 'category', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(propuestas)} gastos reclasificados como inversion.'
        ))

    def _construir_reglas(self, extras):
        """Reglas genericas mas el vocabulario propio que pase el operador."""
        adicionales = {}
        for item in extras:
            if ':' not in item:
                raise CommandError(
                    f'--keyword mal formado: "{item}". Usa categoria:palabra.'
                )
            slug, palabra = item.split(':', 1)
            slug, palabra = slug.strip(), normalizar(palabra.strip())
            if not palabra:
                raise CommandError(f'--keyword sin palabra: "{item}".')
            if slug not in dict(REGLAS):
                raise CommandError(
                    f'--keyword con categoria desconocida: "{slug}". '
                    'Validas: ' + ', '.join(s for s, _ in REGLAS)
                )
            adicionales.setdefault(slug, []).append(palabra)

        if not adicionales:
            return REGLAS_COMPILADAS
        return [
            (slug, patrones + compilar(adicionales.get(slug, [])))
            for slug, patrones in REGLAS_COMPILADAS
        ]

    def _parse_ids(self, raw):
        if not raw:
            return set()
        try:
            return {int(x) for x in raw.split(',') if x.strip()}
        except ValueError:
            raise CommandError('Los IDs deben ser numeros separados por coma.')

    def _clasificar(self, gasto, minimo, forzado=False):
        """Devuelve el slug de la categoria de inversion, o None."""
        texto = normalizar(gasto.description)

        if any(rx.search(texto) for rx in EXCLUSIONES_COMPILADAS):
            return None

        for slug, patrones in getattr(self, 'reglas', REGLAS_COMPILADAS):
            if any(rx.search(texto) for rx in patrones):
                # El montaje de un local se capitaliza completo, sin umbral:
                # son pagos parciales al contratista que juntos son un activo.
                if slug == 'remodeling' or gasto.amount >= minimo or forzado:
                    return slug
                return None

        return 'equipment' if forzado else None

    def _imprimir(self, propuestas, minimo):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nGastos a reclasificar como inversion (umbral ${minimo:,.0f}):\n'
        ))
        encabezado = f"{'ID':>5}  {'Fecha':<11} {'Monto':>13}  {'De':<14} {'A':<14} Descripcion"
        self.stdout.write(encabezado)
        self.stdout.write('-' * len(encabezado))

        total = Decimal('0')
        por_categoria = {}
        por_mes = {}
        for gasto, categoria in propuestas:
            total += gasto.amount
            por_categoria[categoria.name] = por_categoria.get(categoria.name, Decimal('0')) + gasto.amount
            mes = gasto.expense_date.strftime('%Y-%m')
            por_mes[mes] = por_mes.get(mes, Decimal('0')) + gasto.amount
            self.stdout.write(
                f'{gasto.id:>5}  {gasto.expense_date}  {gasto.amount:>13,.0f}  '
                f'{gasto.category.slug:<14} {categoria.slug:<14} {gasto.description[:45]}'
            )

        self.stdout.write('\nPor categoria destino:')
        for nombre, monto in sorted(por_categoria.items(), key=lambda x: -x[1]):
            self.stdout.write(f'  {nombre:<24} ${monto:>14,.0f}')

        self.stdout.write('\nPor mes (asi cambia el margen de cada uno):')
        for mes, monto in sorted(por_mes.items()):
            self.stdout.write(f'  {mes}  ${monto:>14,.0f}')

        self.stdout.write(self.style.SUCCESS(
            f'\nTotal a mover de gasto a inversion: ${total:,.0f} '
            f'({len(propuestas)} registros)'
        ))
