"""Datos estaticos sembrables de la Polla Mundialista 2026.

Fuente: sorteo REAL del Mundial 2026 (12 grupos A-L, 48 selecciones),
verificado cruzando Wikipedia / FIFA.com / ESPN. iso2 = codigo de bandera
(flagcdn). Estos datos los carga el comando ``polla_seed``.
"""

# ── Selecciones por grupo ────────────────────────────────────────────────
# (code, name, iso2, confederation, host)
GROUPS = {
    "A": [
        ("MEX", "México", "mx", "CONCACAF", True),
        ("RSA", "Sudáfrica", "za", "CAF", False),
        ("KOR", "Corea del Sur", "kr", "AFC", False),
        ("CZE", "Rep. Checa", "cz", "UEFA", False),
    ],
    "B": [
        ("CAN", "Canadá", "ca", "CONCACAF", True),
        ("BIH", "Bosnia y H.", "ba", "UEFA", False),
        ("QAT", "Catar", "qa", "AFC", False),
        ("SUI", "Suiza", "ch", "UEFA", False),
    ],
    "C": [
        ("BRA", "Brasil", "br", "CONMEBOL", False),
        ("MAR", "Marruecos", "ma", "CAF", False),
        ("HAI", "Haití", "ht", "CONCACAF", False),
        ("SCO", "Escocia", "gb-sct", "UEFA", False),
    ],
    "D": [
        ("USA", "Estados Unidos", "us", "CONCACAF", True),
        ("PAR", "Paraguay", "py", "CONMEBOL", False),
        ("AUS", "Australia", "au", "AFC", False),
        ("TUR", "Turquía", "tr", "UEFA", False),
    ],
    "E": [
        ("GER", "Alemania", "de", "UEFA", False),
        ("CUW", "Curazao", "cw", "CONCACAF", False),
        ("CIV", "Costa de Marfil", "ci", "CAF", False),
        ("ECU", "Ecuador", "ec", "CONMEBOL", False),
    ],
    "F": [
        ("NED", "Países Bajos", "nl", "UEFA", False),
        ("JPN", "Japón", "jp", "AFC", False),
        ("SWE", "Suecia", "se", "UEFA", False),
        ("TUN", "Túnez", "tn", "CAF", False),
    ],
    "G": [
        ("BEL", "Bélgica", "be", "UEFA", False),
        ("EGY", "Egipto", "eg", "CAF", False),
        ("IRN", "Irán", "ir", "AFC", False),
        ("NZL", "Nueva Zelanda", "nz", "OFC", False),
    ],
    "H": [
        ("ESP", "España", "es", "UEFA", False),
        ("CPV", "Cabo Verde", "cv", "CAF", False),
        ("KSA", "Arabia Saudita", "sa", "AFC", False),
        ("URU", "Uruguay", "uy", "CONMEBOL", False),
    ],
    "I": [
        ("FRA", "Francia", "fr", "UEFA", False),
        ("SEN", "Senegal", "sn", "CAF", False),
        ("IRQ", "Irak", "iq", "AFC", False),
        ("NOR", "Noruega", "no", "UEFA", False),
    ],
    "J": [
        ("ARG", "Argentina", "ar", "CONMEBOL", False),
        ("ALG", "Argelia", "dz", "CAF", False),
        ("AUT", "Austria", "at", "UEFA", False),
        ("JOR", "Jordania", "jo", "AFC", False),
    ],
    "K": [
        ("POR", "Portugal", "pt", "UEFA", False),
        ("COD", "RD Congo", "cd", "CAF", False),
        ("UZB", "Uzbekistán", "uz", "AFC", False),
        ("COL", "Colombia", "co", "CONMEBOL", False),
    ],
    "L": [
        ("ENG", "Inglaterra", "gb-eng", "UEFA", False),
        ("CRO", "Croacia", "hr", "UEFA", False),
        ("GHA", "Ghana", "gh", "CAF", False),
        ("PAN", "Panamá", "pa", "CONCACAF", False),
    ],
}

# ── Jugadores estrella (opciones de Goleador / MVP) ──────────────────────
# (name, team_code). Todos confirmados en sus convocatorias finales de 26
# anunciadas en mayo 2026 (verificado vs FIFA.com / ESPN / federaciones).
STAR_PLAYERS = [
    ("Lionel Messi", "ARG"),
    ("Kylian Mbappé", "FRA"),
    ("Vinícius Jr.", "BRA"),
    ("Erling Haaland", "NOR"),
    ("Harry Kane", "ENG"),
    ("Cristiano Ronaldo", "POR"),
    ("Lamine Yamal", "ESP"),
    ("Luis Díaz", "COL"),
    ("Jude Bellingham", "ENG"),
    ("Julián Álvarez", "ARG"),
    ("Mohamed Salah", "EGY"),
    ("Florian Wirtz", "GER"),
    ("Lautaro Martínez", "ARG"),
    ("Ousmane Dembélé", "FRA"),
    ("Raphinha", "BRA"),
    ("Mikel Oyarzabal", "ESP"),
    ("Pedri", "ESP"),
    ("Jamal Musiala", "GER"),
    ("Kai Havertz", "GER"),
    ("Kevin De Bruyne", "BEL"),
    ("Romelu Lukaku", "BEL"),
    ("Martin Ødegaard", "NOR"),
    ("Bukayo Saka", "ENG"),
    ("James Rodríguez", "COL"),
]

# ── Arqueros (opciones de Guante de Oro) ─────────────────────────────────
# Todos titulares/convocados confirmados (mayo 2026).
STAR_KEEPERS = [
    ("Emiliano Martínez", "ARG"),
    ("Alisson Becker", "BRA"),
    ("Thibaut Courtois", "BEL"),
    ("Unai Simón", "ESP"),
    ("Mike Maignan", "FRA"),
    ("Camilo Vargas", "COL"),
    ("Yassine Bono", "MAR"),
    ("Jordan Pickford", "ENG"),
    ("Diogo Costa", "POR"),
    ("Manuel Neuer", "GER"),
]

# ── Menciones / pronosticos de torneo completo ───────────────────────────
# (code, title, hint, points, award_type, display_order)
AWARDS = [
    ("champion", "Campeón", "¿Quién levanta la copa?", 25, "team", 1),
    ("runnerup", "Subcampeón", "El finalista que pierde", 15, "team", 2),
    ("scorer", "Goleador", "Bota de Oro del torneo", 35, "player", 3),
    ("mvp", "MVP", "Mejor jugador (Balón de Oro)", 10, "player", 4),
    ("glove", "Guante de Oro", "Mejor arquero", 10, "keeper", 5),
]

# ── Misiones ──────────────────────────────────────────────────────────────
# (code, title, desc, reward, kind, target, bonus_points, param, order)
MISSIONS = [
    (
        "first", "Tu primer pronóstico",
        "Pronostica el marcador de cualquier partido.",
        "+10 pts", "first_prediction", 1, 10, "", 1,
    ),
    (
        "exact3", "Ojo de halcón",
        "Acierta 3 marcadores exactos.",
        "+25 pts", "exact_scores", 3, 25, "", 2,
    ),
    (
        "colombia", "Mi corazón es tricolor",
        "Pronostica todos los partidos de Colombia.",
        "Insignia ⭐", "team_matches", 3, 0, "COL", 3,
    ),
    (
        "streak5", "En racha",
        "Acierta 5 resultados seguidos.",
        "+40 pts", "streak", 5, 40, "", 4,
    ),
    (
        "groupstage", "Maestro de grupos",
        "Pronostica al menos 60 partidos de la fase de grupos.",
        "+20 pts", "group_stage_complete", 60, 20, "", 5,
    ),
    # "invite" se gestiona como feature propia de referidos (no como misión),
    # con su tarjeta dedicada en la pestaña Misiones. Ver apps.polla.Referral.
]

# Metadatos de torneo
TOURNAMENT = {
    "name": "Mundial 2026",
    "slug": "mundial-2026",
    "kickoff": "2026-06-11T19:00:00Z",
    "prize": "$500.000",
    "prize_note": "En efectivo · un solo ganador",
    "api_league_id": 1,
    "api_season": 2026,
}
