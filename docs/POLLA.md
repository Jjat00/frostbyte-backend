# Polla Mundialista 2026 — Backend

App `apps.polla`: juego de pronósticos del Mundial 2026 para clientes (login con
Google). El cliente pronostica el marcador de cada partido, elige "menciones"
de torneo (campeón, goleador, …) y compite en una tabla de posiciones. El
puntaje se recalcula automáticamente cuando llegan los resultados.

## Sistema de puntos

| Resultado del pronóstico | Puntos |
| --- | --- |
| Marcador exacto (2-1 y quedó 2-1) | **3** |
| Resultado correcto (ganador/empate, marcador distinto) | **1** |
| Incorrecto | **0** |

Menciones (una sola elección por torneo, valen al resolverse): Campeón 25 ·
Subcampeón 15 · Goleador 35 · MVP 10 · Guante de Oro 10.

**Avance (bracket de eliminación)** — puntos extra por acertar quién avanza,
escalados por ronda: Dieciseisavos **2** · Octavos **4** · Cuartos **7** ·
Semifinal **12** · Tercer puesto **5** · Campeón (final) **20**. Suman aparte de
los marcadores y de las menciones (todo va a `UserScore.points`).

## Datos reales (API-Football) — opcional

El backend funciona con el **calendario sembrado** (104 partidos reales,
verificados). Si se configura `API_FOOTBALL_KEY`, `polla_sync` trae el estado de
los partidos (en vivo, marcadores, finalizados) desde API-Football y lo aplica
mapeando por `api_fixture_id`.

```env
API_FOOTBALL_KEY=...            # vacío = solo datos sembrados
API_FOOTBALL_LEAGUE_ID=1        # 1 = FIFA World Cup
API_FOOTBALL_SEASON=2026
```

> Para enlazar partidos/selecciones con los IDs de API-Football, completa
> `Match.api_fixture_id` y `Team.api_team_id` (admin o un comando de mapeo).

## Comandos

```bash
python manage.py polla_seed              # carga torneo, grupos, selecciones,
                                         # jugadores, menciones, misiones y los
                                         # 104 partidos (idempotente)
python manage.py polla_sync              # trae resultados (si hay key) y
                                         # recalcula puntajes/ranking/misiones
python manage.py polla_sync --no-fetch   # solo recalcular (sin API externa)
python manage.py polla_demo              # poblar con participantes/resultados
                                         # de demo (QA), --reset para limpiar
```

## Cron en Railway

Durante el torneo, `polla_sync` debe correr periódicamente. En Railway se crea
un **servicio adicional** (cron) apuntando al mismo repo:

- Start command: `python manage.py polla_sync`
- Cron schedule: `*/5 * * * *` (cada 5 minutos)

El comando es idempotente y nunca rompe por errores de la API externa (los
captura y solo registra). Recalcular sin partidos nuevos no cambia el estado.

## Endpoints (`/api/v1/polla/`)

Lecturas públicas (se enriquecen con datos del usuario si viene el JWT de
cliente); escrituras requieren sesión de cliente (`Authorization: Bearer`).

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `tournament/` | Meta del torneo (pitazo, premio, conteos). |
| GET | `groups/` | 12 grupos con sus selecciones. |
| GET | `standings/` | Tabla de posiciones por grupo (calculada en vivo). |
| GET | `matches/` | Partidos. Filtros: `?status=`, `?stage=`, `?group=`, `?team=`, `?featured=1`. Incluye `my_prediction` si autenticado. |
| GET | `matches/<slug>/` | Detalle de un partido. |
| PUT/POST | `matches/<slug>/predict/` | Crea/actualiza el pronóstico `{home_score, away_score}`. Rechaza si el partido está cerrado (pitazo). **Auth.** |
| GET | `predictions/me/` | Mis pronósticos. **Auth.** |
| GET | `awards/` | Menciones + opciones (`team`/`player`/`keeper`) + mi elección. |
| PUT | `awards/<code>/pick/` | Elige mención: `{team_code}` o `{player_id}`. **Auth.** |
| GET | `bracket/` | Llave de eliminación encadenada del usuario (ver abajo). |
| PUT | `bracket/<slug>/pick/` | Elige quién avanza del cruce: `{winner_code}`. Devuelve la llave ya propagada. **Auth.** |
| GET | `ranking/` | Tabla de posiciones (top 100 + mi fila). |
| GET | `missions/` | Misiones con mi progreso + `my_stats`. |
| GET | `me/stats/` | Mis estadísticas (puntos, posición, exactos, …). **Auth.** |

### Forma de un partido (`matches/`)

```json
{
  "slug": "m1", "number": 1, "stage": "group", "stage_display": "Fase de grupos",
  "group": "A", "round_label": "Jornada 1",
  "home": {"code": "MEX", "name": "México", "iso2": "mx", "placeholder": null},
  "away": {"code": "RSA", "name": "Sudáfrica", "iso2": "za", "placeholder": null},
  "kickoff": "2026-06-11T14:00:00-05:00",
  "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca",
  "status": "upcoming", "minute": null, "home_score": null, "away_score": null,
  "featured": true, "is_locked": false,
  "my_prediction": {"home_score": 2, "away_score": 1, "points_earned": 0,
                    "is_exact": false, "is_correct_outcome": false, "scored": false}
}
```

En eliminatorias `home.code`/`away.code` son `null` y `home.placeholder` trae el
slot del bracket (p.ej. `"1A"`, `"Ganador 73"`).

## Bracket encadenado (fase de eliminación)

La llave es **encadenada y personal**: no usa los equipos reales, usa los que el
usuario clasificó según SUS marcadores de grupo.

1. Los marcadores de los 72 partidos de grupos definen la **tabla pronosticada**
   del usuario (mismos desempates que `standings/`): 1.º/2.º de cada grupo + los
   **8 mejores terceros**, que siembran los 16 dieciseisavos (R32).
2. De octavos en adelante, cada cruce toma los **ganadores que el propio usuario
   eligió** (`BracketPick`); el pick se propaga al cruce siguiente (`"Ganador N"`)
   y el tercer puesto toma los perdedores de semifinal (`"Perdedor N"`).
3. **Desbloqueo:** la llave se abre cuando el usuario tiene los **72** pronósticos
   de grupos (`unlocked`). Antes, devuelve la estructura con los slots por definir.
4. **Puntaje por avance:** al finalizar cada cruce real, se compara el ganador
   elegido por el usuario contra el ganador real, escalado por ronda (ver arriba).
   Se recalcula en `recompute_bracket` (parte de `recompute_all`).

Lógica en `apps/polla/bracket.py`. Cambiar un pick de arriba **invalida en
cascada** (poda) los picks de abajo que dejan de ser válidos.

```json
{
  "unlocked": true, "group_predicted": 72, "group_total": 72,
  "champion": {"code": "ARG", "name": "Argentina", "iso2": "ar"},
  "rounds": [
    { "stage": "r32", "label": "Dieciseisavos", "points": 2, "matches": [
      { "slug": "m73", "number": 73, "stage": "r32", "round_label": "Dieciseisavos",
        "kickoff": "2026-06-28T14:00:00-05:00",
        "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium",
        "status": "upcoming", "minute": null, "home_score": null, "away_score": null,
        "home": {"code": "CZE", "name": "Rep. Checa", "iso2": "cz"},
        "away": {"code": "SUI", "name": "Suiza", "iso2": "ch"},
        "home_source": "2A", "away_source": "2B",
        "my_pick": "CZE", "my_prediction": null, "is_locked": false }
    ]}
  ]
}
```
