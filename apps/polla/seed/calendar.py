"""Calendario COMPLETO del Mundial 2026 (semilla para ``polla_seed``).

104 partidos (72 de fase de grupos + 32 de eliminatorias). Generado a partir de
una investigacion multi-fuente (Wikipedia / FIFA.com / ESPN) verificada
(confianza alta, 104/104 partidos, anclado en el inaugural MEX-RSA).

Cada entrada:
  number     int   numero oficial de partido (1..104)
  stage      str   group|r32|r16|qf|sf|third|final
  group      str   letra A-L (vacio si eliminatoria)
  round_label str  "Jornada 1", "Octavos", "Final", ...
  home/away  str   codigo de seleccion (vacio si aun no se conoce)
  home_placeholder/away_placeholder  str  slot de bracket ("1A","Ganador 73")
  kickoff    str   ISO 8601 en UTC
  venue_city/venue_stadium  str
  featured   bool  partido destacado (inaugural y los de Colombia)

``polla_seed`` es idempotente (update_or_create por ``number``). Cuando se
configure API_FOOTBALL_KEY, ``polla_sync`` sobreescribe estado/marcadores en vivo.
"""

FIXTURES = [

    # -- Grupo A Jornada 1 --
    {"number": 1, "stage": "group", "group": "A", "round_label": "Jornada 1", "home": "MEX", "away": "RSA", "kickoff": "2026-06-11T19:00:00Z", "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca", "featured": True},
    {"number": 2, "stage": "group", "group": "A", "round_label": "Jornada 1", "home": "KOR", "away": "CZE", "kickoff": "2026-06-12T02:00:00Z", "venue_city": "Zapopan (Guadalajara)", "venue_stadium": "Estadio Akron"},
    # -- Grupo B Jornada 1 --
    {"number": 3, "stage": "group", "group": "B", "round_label": "Jornada 1", "home": "CAN", "away": "BIH", "kickoff": "2026-06-12T19:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    # -- Grupo D Jornada 1 --
    {"number": 4, "stage": "group", "group": "D", "round_label": "Jornada 1", "home": "USA", "away": "PAR", "kickoff": "2026-06-13T01:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    # -- Grupo C Jornada 1 --
    {"number": 5, "stage": "group", "group": "C", "round_label": "Jornada 1", "home": "HAI", "away": "SCO", "kickoff": "2026-06-14T01:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    # -- Grupo D Jornada 1 --
    {"number": 6, "stage": "group", "group": "D", "round_label": "Jornada 1", "home": "AUS", "away": "TUR", "kickoff": "2026-06-14T04:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    # -- Grupo C Jornada 1 --
    {"number": 7, "stage": "group", "group": "C", "round_label": "Jornada 1", "home": "BRA", "away": "MAR", "kickoff": "2026-06-13T22:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    # -- Grupo B Jornada 1 --
    {"number": 8, "stage": "group", "group": "B", "round_label": "Jornada 1", "home": "QAT", "away": "SUI", "kickoff": "2026-06-13T19:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    # -- Grupo E Jornada 1 --
    {"number": 9, "stage": "group", "group": "E", "round_label": "Jornada 1", "home": "CIV", "away": "ECU", "kickoff": "2026-06-14T23:00:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    {"number": 10, "stage": "group", "group": "E", "round_label": "Jornada 1", "home": "GER", "away": "CUW", "kickoff": "2026-06-14T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    # -- Grupo F Jornada 1 --
    {"number": 11, "stage": "group", "group": "F", "round_label": "Jornada 1", "home": "NED", "away": "JPN", "kickoff": "2026-06-14T20:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 12, "stage": "group", "group": "F", "round_label": "Jornada 1", "home": "SWE", "away": "TUN", "kickoff": "2026-06-15T02:00:00Z", "venue_city": "Guadalupe (Monterrey)", "venue_stadium": "Estadio BBVA"},
    # -- Grupo H Jornada 1 --
    {"number": 13, "stage": "group", "group": "H", "round_label": "Jornada 1", "home": "KSA", "away": "URU", "kickoff": "2026-06-15T22:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    {"number": 14, "stage": "group", "group": "H", "round_label": "Jornada 1", "home": "ESP", "away": "CPV", "kickoff": "2026-06-15T16:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Grupo G Jornada 1 --
    {"number": 15, "stage": "group", "group": "G", "round_label": "Jornada 1", "home": "IRN", "away": "NZL", "kickoff": "2026-06-16T01:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 16, "stage": "group", "group": "G", "round_label": "Jornada 1", "home": "BEL", "away": "EGY", "kickoff": "2026-06-15T19:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    # -- Grupo I Jornada 1 --
    {"number": 17, "stage": "group", "group": "I", "round_label": "Jornada 1", "home": "FRA", "away": "SEN", "kickoff": "2026-06-16T19:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    {"number": 18, "stage": "group", "group": "I", "round_label": "Jornada 1", "home": "IRQ", "away": "NOR", "kickoff": "2026-06-16T22:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    # -- Grupo J Jornada 1 --
    {"number": 19, "stage": "group", "group": "J", "round_label": "Jornada 1", "home": "ARG", "away": "ALG", "kickoff": "2026-06-17T01:00:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    {"number": 20, "stage": "group", "group": "J", "round_label": "Jornada 1", "home": "AUT", "away": "JOR", "kickoff": "2026-06-17T04:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    # -- Grupo L Jornada 1 --
    {"number": 21, "stage": "group", "group": "L", "round_label": "Jornada 1", "home": "GHA", "away": "PAN", "kickoff": "2026-06-17T23:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    {"number": 22, "stage": "group", "group": "L", "round_label": "Jornada 1", "home": "ENG", "away": "CRO", "kickoff": "2026-06-17T20:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    # -- Grupo K Jornada 1 --
    {"number": 23, "stage": "group", "group": "K", "round_label": "Jornada 1", "home": "POR", "away": "COD", "kickoff": "2026-06-17T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 24, "stage": "group", "group": "K", "round_label": "Jornada 1", "home": "UZB", "away": "COL", "kickoff": "2026-06-18T02:00:00Z", "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca", "featured": True},
    # -- Grupo A Jornada 2 --
    {"number": 25, "stage": "group", "group": "A", "round_label": "Jornada 2", "home": "CZE", "away": "RSA", "kickoff": "2026-06-18T16:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Grupo B Jornada 2 --
    {"number": 26, "stage": "group", "group": "B", "round_label": "Jornada 2", "home": "SUI", "away": "BIH", "kickoff": "2026-06-18T19:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 27, "stage": "group", "group": "B", "round_label": "Jornada 2", "home": "CAN", "away": "QAT", "kickoff": "2026-06-18T22:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    # -- Grupo A Jornada 2 --
    {"number": 28, "stage": "group", "group": "A", "round_label": "Jornada 2", "home": "MEX", "away": "KOR", "kickoff": "2026-06-19T01:00:00Z", "venue_city": "Zapopan (Guadalajara)", "venue_stadium": "Estadio Akron"},
    # -- Grupo C Jornada 2 --
    {"number": 29, "stage": "group", "group": "C", "round_label": "Jornada 2", "home": "BRA", "away": "HAI", "kickoff": "2026-06-20T00:30:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    {"number": 30, "stage": "group", "group": "C", "round_label": "Jornada 2", "home": "SCO", "away": "MAR", "kickoff": "2026-06-19T22:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    # -- Grupo D Jornada 2 --
    {"number": 31, "stage": "group", "group": "D", "round_label": "Jornada 2", "home": "TUR", "away": "PAR", "kickoff": "2026-06-20T03:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    {"number": 32, "stage": "group", "group": "D", "round_label": "Jornada 2", "home": "USA", "away": "AUS", "kickoff": "2026-06-19T19:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    # -- Grupo E Jornada 2 --
    {"number": 33, "stage": "group", "group": "E", "round_label": "Jornada 2", "home": "GER", "away": "CIV", "kickoff": "2026-06-20T20:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    {"number": 34, "stage": "group", "group": "E", "round_label": "Jornada 2", "home": "ECU", "away": "CUW", "kickoff": "2026-06-21T00:00:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    # -- Grupo F Jornada 2 --
    {"number": 35, "stage": "group", "group": "F", "round_label": "Jornada 2", "home": "NED", "away": "SWE", "kickoff": "2026-06-20T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 36, "stage": "group", "group": "F", "round_label": "Jornada 2", "home": "TUN", "away": "JPN", "kickoff": "2026-06-21T04:00:00Z", "venue_city": "Guadalupe (Monterrey)", "venue_stadium": "Estadio BBVA"},
    # -- Grupo H Jornada 2 --
    {"number": 37, "stage": "group", "group": "H", "round_label": "Jornada 2", "home": "URU", "away": "CPV", "kickoff": "2026-06-21T22:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    {"number": 38, "stage": "group", "group": "H", "round_label": "Jornada 2", "home": "ESP", "away": "KSA", "kickoff": "2026-06-21T16:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Grupo G Jornada 2 --
    {"number": 39, "stage": "group", "group": "G", "round_label": "Jornada 2", "home": "BEL", "away": "IRN", "kickoff": "2026-06-21T19:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 40, "stage": "group", "group": "G", "round_label": "Jornada 2", "home": "NZL", "away": "EGY", "kickoff": "2026-06-22T01:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    # -- Grupo I Jornada 2 --
    {"number": 41, "stage": "group", "group": "I", "round_label": "Jornada 2", "home": "NOR", "away": "SEN", "kickoff": "2026-06-23T00:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    {"number": 42, "stage": "group", "group": "I", "round_label": "Jornada 2", "home": "FRA", "away": "IRQ", "kickoff": "2026-06-22T21:00:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    # -- Grupo J Jornada 2 --
    {"number": 43, "stage": "group", "group": "J", "round_label": "Jornada 2", "home": "ARG", "away": "AUT", "kickoff": "2026-06-22T17:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 44, "stage": "group", "group": "J", "round_label": "Jornada 2", "home": "JOR", "away": "ALG", "kickoff": "2026-06-23T03:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    # -- Grupo L Jornada 2 --
    {"number": 45, "stage": "group", "group": "L", "round_label": "Jornada 2", "home": "ENG", "away": "GHA", "kickoff": "2026-06-23T20:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    {"number": 46, "stage": "group", "group": "L", "round_label": "Jornada 2", "home": "PAN", "away": "CRO", "kickoff": "2026-06-23T23:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    # -- Grupo K Jornada 2 --
    {"number": 47, "stage": "group", "group": "K", "round_label": "Jornada 2", "home": "POR", "away": "UZB", "kickoff": "2026-06-23T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 48, "stage": "group", "group": "K", "round_label": "Jornada 2", "home": "COL", "away": "COD", "kickoff": "2026-06-24T02:00:00Z", "venue_city": "Zapopan (Guadalajara)", "venue_stadium": "Estadio Akron", "featured": True},
    # -- Grupo C Jornada 3 --
    {"number": 49, "stage": "group", "group": "C", "round_label": "Jornada 3", "home": "SCO", "away": "BRA", "kickoff": "2026-06-24T22:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    {"number": 50, "stage": "group", "group": "C", "round_label": "Jornada 3", "home": "MAR", "away": "HAI", "kickoff": "2026-06-24T22:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Grupo B Jornada 3 --
    {"number": 51, "stage": "group", "group": "B", "round_label": "Jornada 3", "home": "SUI", "away": "CAN", "kickoff": "2026-06-24T19:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    {"number": 52, "stage": "group", "group": "B", "round_label": "Jornada 3", "home": "BIH", "away": "QAT", "kickoff": "2026-06-24T19:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    # -- Grupo A Jornada 3 --
    {"number": 53, "stage": "group", "group": "A", "round_label": "Jornada 3", "home": "CZE", "away": "MEX", "kickoff": "2026-06-25T01:00:00Z", "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca"},
    {"number": 54, "stage": "group", "group": "A", "round_label": "Jornada 3", "home": "RSA", "away": "KOR", "kickoff": "2026-06-25T01:00:00Z", "venue_city": "Guadalupe (Monterrey)", "venue_stadium": "Estadio BBVA"},
    # -- Grupo E Jornada 3 --
    {"number": 55, "stage": "group", "group": "E", "round_label": "Jornada 3", "home": "CUW", "away": "CIV", "kickoff": "2026-06-25T20:00:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    {"number": 56, "stage": "group", "group": "E", "round_label": "Jornada 3", "home": "ECU", "away": "GER", "kickoff": "2026-06-25T20:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    # -- Grupo F Jornada 3 --
    {"number": 57, "stage": "group", "group": "F", "round_label": "Jornada 3", "home": "JPN", "away": "SWE", "kickoff": "2026-06-25T23:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 58, "stage": "group", "group": "F", "round_label": "Jornada 3", "home": "TUN", "away": "NED", "kickoff": "2026-06-25T23:00:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    # -- Grupo D Jornada 3 --
    {"number": 59, "stage": "group", "group": "D", "round_label": "Jornada 3", "home": "TUR", "away": "USA", "kickoff": "2026-06-26T02:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 60, "stage": "group", "group": "D", "round_label": "Jornada 3", "home": "PAR", "away": "AUS", "kickoff": "2026-06-26T02:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    # -- Grupo I Jornada 3 --
    {"number": 61, "stage": "group", "group": "I", "round_label": "Jornada 3", "home": "NOR", "away": "FRA", "kickoff": "2026-06-26T19:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    {"number": 62, "stage": "group", "group": "I", "round_label": "Jornada 3", "home": "SEN", "away": "IRQ", "kickoff": "2026-06-26T19:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    # -- Grupo G Jornada 3 --
    {"number": 63, "stage": "group", "group": "G", "round_label": "Jornada 3", "home": "EGY", "away": "IRN", "kickoff": "2026-06-27T03:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    {"number": 64, "stage": "group", "group": "G", "round_label": "Jornada 3", "home": "NZL", "away": "BEL", "kickoff": "2026-06-27T03:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    # -- Grupo H Jornada 3 --
    {"number": 65, "stage": "group", "group": "H", "round_label": "Jornada 3", "home": "CPV", "away": "KSA", "kickoff": "2026-06-27T00:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 66, "stage": "group", "group": "H", "round_label": "Jornada 3", "home": "URU", "away": "ESP", "kickoff": "2026-06-27T00:00:00Z", "venue_city": "Zapopan (Guadalajara)", "venue_stadium": "Estadio Akron"},
    # -- Grupo L Jornada 3 --
    {"number": 67, "stage": "group", "group": "L", "round_label": "Jornada 3", "home": "PAN", "away": "ENG", "kickoff": "2026-06-27T21:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    {"number": 68, "stage": "group", "group": "L", "round_label": "Jornada 3", "home": "CRO", "away": "GHA", "kickoff": "2026-06-27T21:00:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    # -- Grupo J Jornada 3 --
    {"number": 69, "stage": "group", "group": "J", "round_label": "Jornada 3", "home": "ALG", "away": "AUT", "kickoff": "2026-06-28T02:00:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    {"number": 70, "stage": "group", "group": "J", "round_label": "Jornada 3", "home": "JOR", "away": "ARG", "kickoff": "2026-06-28T02:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    # -- Grupo K Jornada 3 --
    {"number": 71, "stage": "group", "group": "K", "round_label": "Jornada 3", "home": "COL", "away": "POR", "kickoff": "2026-06-27T23:30:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium", "featured": True},
    {"number": 72, "stage": "group", "group": "K", "round_label": "Jornada 3", "home": "COD", "away": "UZB", "kickoff": "2026-06-27T23:30:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Dieciseisavos --
    {"number": 73, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "2A", "away_placeholder": "2B", "kickoff": "2026-06-28T19:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 74, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1E", "away_placeholder": "3ro A/B/C/D/F", "kickoff": "2026-06-29T20:30:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    {"number": 75, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1F", "away_placeholder": "2C", "kickoff": "2026-06-30T01:00:00Z", "venue_city": "Guadalupe (Monterrey)", "venue_stadium": "Estadio BBVA"},
    {"number": 76, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1C", "away_placeholder": "2F", "kickoff": "2026-06-29T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 77, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1I", "away_placeholder": "3ro C/D/F/G/H", "kickoff": "2026-06-30T21:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    {"number": 78, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "2E", "away_placeholder": "2I", "kickoff": "2026-06-30T17:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 79, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1A", "away_placeholder": "3ro C/E/F/H/I", "kickoff": "2026-07-01T01:00:00Z", "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca"},
    {"number": 80, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1L", "away_placeholder": "3ro E/H/I/J/K", "kickoff": "2026-07-01T16:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    {"number": 81, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1D", "away_placeholder": "3ro B/E/F/I/J", "kickoff": "2026-07-02T00:00:00Z", "venue_city": "Santa Clara (San Francisco Bay Area)", "venue_stadium": "Levi's Stadium"},
    {"number": 82, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1G", "away_placeholder": "3ro A/E/H/I/J", "kickoff": "2026-07-01T20:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    {"number": 83, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "2K", "away_placeholder": "2L", "kickoff": "2026-07-02T23:00:00Z", "venue_city": "Toronto", "venue_stadium": "BMO Field"},
    {"number": 84, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1H", "away_placeholder": "2J", "kickoff": "2026-07-02T19:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 85, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1B", "away_placeholder": "3ro E/F/G/I/J", "kickoff": "2026-07-03T03:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    {"number": 86, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1J", "away_placeholder": "2H", "kickoff": "2026-07-03T22:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    {"number": 87, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "1K", "away_placeholder": "3ro D/E/I/J/L", "kickoff": "2026-07-04T01:30:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    {"number": 88, "stage": "r32", "round_label": "Dieciseisavos", "home_placeholder": "2D", "away_placeholder": "2G", "kickoff": "2026-07-03T18:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    # -- Octavos --
    {"number": 89, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 74", "away_placeholder": "Ganador 77", "kickoff": "2026-07-04T21:00:00Z", "venue_city": "Philadelphia", "venue_stadium": "Lincoln Financial Field"},
    {"number": 90, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 73", "away_placeholder": "Ganador 75", "kickoff": "2026-07-04T17:00:00Z", "venue_city": "Houston", "venue_stadium": "NRG Stadium"},
    {"number": 91, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 76", "away_placeholder": "Ganador 78", "kickoff": "2026-07-05T20:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
    {"number": 92, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 79", "away_placeholder": "Ganador 80", "kickoff": "2026-07-06T00:00:00Z", "venue_city": "Ciudad de Mexico", "venue_stadium": "Estadio Azteca"},
    {"number": 93, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 83", "away_placeholder": "Ganador 84", "kickoff": "2026-07-06T19:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 94, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 81", "away_placeholder": "Ganador 82", "kickoff": "2026-07-07T00:00:00Z", "venue_city": "Seattle", "venue_stadium": "Lumen Field"},
    {"number": 95, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 86", "away_placeholder": "Ganador 88", "kickoff": "2026-07-07T16:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    {"number": 96, "stage": "r16", "round_label": "Octavos", "home_placeholder": "Ganador 85", "away_placeholder": "Ganador 87", "kickoff": "2026-07-07T20:00:00Z", "venue_city": "Vancouver", "venue_stadium": "BC Place"},
    # -- Cuartos de final --
    {"number": 97, "stage": "qf", "round_label": "Cuartos de final", "home_placeholder": "Ganador 89", "away_placeholder": "Ganador 90", "kickoff": "2026-07-09T20:00:00Z", "venue_city": "Foxborough (Boston)", "venue_stadium": "Gillette Stadium"},
    {"number": 98, "stage": "qf", "round_label": "Cuartos de final", "home_placeholder": "Ganador 93", "away_placeholder": "Ganador 94", "kickoff": "2026-07-10T19:00:00Z", "venue_city": "Inglewood (Los Angeles)", "venue_stadium": "SoFi Stadium"},
    {"number": 99, "stage": "qf", "round_label": "Cuartos de final", "home_placeholder": "Ganador 91", "away_placeholder": "Ganador 92", "kickoff": "2026-07-11T21:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    {"number": 100, "stage": "qf", "round_label": "Cuartos de final", "home_placeholder": "Ganador 95", "away_placeholder": "Ganador 96", "kickoff": "2026-07-12T01:00:00Z", "venue_city": "Kansas City", "venue_stadium": "Arrowhead Stadium"},
    # -- Semifinal --
    {"number": 101, "stage": "sf", "round_label": "Semifinal", "home_placeholder": "Ganador 97", "away_placeholder": "Ganador 98", "kickoff": "2026-07-14T19:00:00Z", "venue_city": "Arlington (Dallas)", "venue_stadium": "AT&T Stadium"},
    {"number": 102, "stage": "sf", "round_label": "Semifinal", "home_placeholder": "Ganador 99", "away_placeholder": "Ganador 100", "kickoff": "2026-07-15T19:00:00Z", "venue_city": "Atlanta", "venue_stadium": "Mercedes-Benz Stadium"},
    # -- Tercer puesto --
    {"number": 103, "stage": "third", "round_label": "Tercer puesto", "home_placeholder": "Perdedor 101", "away_placeholder": "Perdedor 102", "kickoff": "2026-07-18T21:00:00Z", "venue_city": "Miami Gardens (Miami)", "venue_stadium": "Hard Rock Stadium"},
    # -- Final --
    {"number": 104, "stage": "final", "round_label": "Final", "home_placeholder": "Ganador 101", "away_placeholder": "Ganador 102", "kickoff": "2026-07-19T19:00:00Z", "venue_city": "East Rutherford (Nueva York/Nueva Jersey)", "venue_stadium": "MetLife Stadium"},
]



def normalized_fixtures():
    """Devuelve FIXTURES con todas las claves presentes (defaults seguros)."""
    out = []
    for f in FIXTURES:
        out.append({
            "number": f["number"],
            "stage": f.get("stage", "group"),
            "group": f.get("group", ""),
            "round_label": f.get("round_label", ""),
            "home": f.get("home", ""),
            "away": f.get("away", ""),
            "home_placeholder": f.get("home_placeholder", ""),
            "away_placeholder": f.get("away_placeholder", ""),
            "kickoff": f["kickoff"],
            "venue_city": f.get("venue_city", ""),
            "venue_stadium": f.get("venue_stadium", ""),
            "featured": f.get("featured", False),
        })
    return out
