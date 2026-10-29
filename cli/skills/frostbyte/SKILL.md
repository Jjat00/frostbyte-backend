---
name: frostbyte
description: Lee y edita Frostbyte (bar Frostbyte y Frostbyte Food) con el CLI `frostbyte`: carta e imágenes de productos, crear y editar productos y variantes, ventas, productos más vendidos, finanzas del mes, pedidos y estado del local. Úsala cuando haya que consultar o cambiar algo de la carta o los números de Frostbyte, aunque no se nombre el CLI.
---

# frostbyte CLI

El CLI habla con la API de producción de Frostbyte. La salida de los comandos es JSON cuando stdout no es una TTY (o con `--json`); los errores salen por stderr como `{"error":{"code","message"}}`. Excepción: `--help` imprime texto (`--help --json` imprime el schema).

Antes de nada: `frostbyte schema` describe todos los comandos, flags y salidas.

## Sesión

- `frostbyte whoami` dice si hay sesión. Si sale código 3, pide al humano que corra `! frostbyte login --username <usuario>` (la contraseña se pide sin eco; nunca la pidas tú en el chat).
- La carta (business, category, product ls/show/image) es pública y no necesita sesión. Ventas, finanzas, pedidos y escrituras exigen un usuario admin.

## Leer

```bash
frostbyte product ls                         # carta activa (los dos negocios)
frostbyte product ls -b frostbyte-food       # solo comida
frostbyte product ls -c granizados -q mora   # por categoría y búsqueda
frostbyte product ls --all                   # incluye inactivos
frostbyte product ls --without-image         # los que no tienen foto
frostbyte product show <slug|id>             # detalle con variantes y opciones
frostbyte product image <slug> --out ./imgs  # DESCARGA el archivo (para solo la URL: product show)
frostbyte product image --all -b frostbyte --out ./imgs
frostbyte category ls                        # slugs de categoría para crear productos
frostbyte sales summary --date today         # ingresos, pedidos, por método de pago
frostbyte sales products --date month --limit 10   # más vendidos por ingresos
frostbyte sales summary --from 2026-10-01 --to 2026-10-15
frostbyte finance summary [--year 2026 --month 9]  # KPIs del mes vs anterior e interanual
frostbyte order ls --date today [--open]      # {count, results}; --date: today|yesterday|week|month
frostbyte store status
```

En `sales`, `--date`: today, yesterday, week, month, last_month, year. `-b`: frostbyte o frostbyte-food.

## Escribir (va a producción)

Siempre en dos pasos: primero `--dry-run`, mostrar el resultado al humano, y solo con su OK correr sin `--dry-run`.

```bash
frostbyte product create --name "Granizado de Lulo" -c granizados \
  --description "…" --variant "Pequeño=8000" --variant "Grande=10000" \
  --image ./lulo.png --dry-run
frostbyte product set <slug> --description "…" --image ./nueva.png --dry-run
frostbyte product set <slug> --active false        # sacar de la carta (reversible)
frostbyte product set <slug> --coming-soon true
frostbyte variant add <slug> --name Extragrande --price 14000
frostbyte variant set <variant-id> --price 9000 [--default]
frostbyte product deactivate <slug> --yes          # igual que --active false
frostbyte image upload ./foto.png                  # devuelve {url}
```

- Precios: `12000` o `12000.50`. Sin separador de miles: `12.000` se rechaza.
- La primera `--variant` queda por defecto; `--price N` crea una sola variante "Único".
- La API no crea producto y variantes de forma atómica: `product create` lo crea inactivo, agrega las variantes y lo activa al final. Si falla a medias, el error trae `partial` y `nextSteps` con los comandos para terminarlo; no repitas `create`.
- `--image` acepta un archivo local (se sube a R2) o una URL pública.
- El negocio sale de la categoría: para Frostbyte Food usa una categoría de ese negocio.
- `product create` se niega si ya existe un producto con el mismo nombre (aunque esté inactivo): en ese caso usa `product set`.
- Nada se borra de verdad: desactivar es reversible.
- Textos visibles al cliente: nunca mencionar gramos.
- Cada escritura queda en `frostbyte audit`.
- `--dry-run` no envía nada ni toca la sesión.

## Códigos de salida

0 ok · 1 error de API o red · 2 error de uso · 3 sin sesión o sin permiso · 4 no encontrado.
