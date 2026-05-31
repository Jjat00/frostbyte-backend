"""Carga (idempotente) los datos base de la Polla Mundialista 2026.

Crea/actualiza: torneo, grupos, selecciones, jugadores, menciones, misiones y
el calendario de partidos. Reejecutar es seguro (update_or_create).

    python manage.py polla_seed
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.polla.models import (
    Award,
    Group,
    Match,
    Mission,
    Player,
    Team,
    Tournament,
)
from apps.polla.seed import static_data as sd
from apps.polla.seed.calendar import normalized_fixtures

GROUP_NAMES = {l: f"Grupo {l}" for l in "ABCDEFGHIJKL"}


class Command(BaseCommand):
    help = "Carga los datos base de la Polla Mundialista 2026 (idempotente)."

    @transaction.atomic
    def handle(self, *args, **options):
        self._seed_tournament()
        groups = self._seed_groups()
        teams = self._seed_teams(groups)
        self._seed_players(teams)
        self._seed_awards()
        self._seed_missions()
        self._seed_matches(groups, teams)
        self.stdout.write(self.style.SUCCESS("Seed de la Polla completado."))

    def _seed_tournament(self):
        t = sd.TOURNAMENT
        Tournament.objects.update_or_create(
            slug=t["slug"],
            defaults={
                "name": t["name"],
                "kickoff": parse_datetime(t["kickoff"]),
                "prize": t["prize"],
                "prize_note": t["prize_note"],
                "api_league_id": t["api_league_id"],
                "api_season": t["api_season"],
                "is_active": True,
            },
        )

    def _seed_groups(self):
        groups = {}
        for i, letter in enumerate("ABCDEFGHIJKL"):
            g, _ = Group.objects.update_or_create(
                letter=letter,
                defaults={"name": GROUP_NAMES[letter], "display_order": i + 1},
            )
            groups[letter] = g
        return groups

    def _seed_teams(self, groups):
        teams = {}
        for letter, members in sd.GROUPS.items():
            for code, name, iso2, conf, host in members:
                team, _ = Team.objects.update_or_create(
                    code=code,
                    defaults={
                        "name": name, "iso2": iso2, "confederation": conf,
                        "group": groups[letter], "is_host": host,
                    },
                )
                teams[code] = team
        return teams

    def _seed_players(self, teams):
        for name, code in sd.STAR_PLAYERS:
            if code in teams:
                Player.objects.update_or_create(
                    name=name, team=teams[code], defaults={"is_keeper": False}
                )
        for name, code in sd.STAR_KEEPERS:
            if code in teams:
                Player.objects.update_or_create(
                    name=name, team=teams[code], defaults={"is_keeper": True}
                )

    def _seed_awards(self):
        for code, title, hint, points, atype, order in sd.AWARDS:
            Award.objects.update_or_create(
                code=code,
                defaults={
                    "title": title, "hint": hint, "points": points,
                    "award_type": atype, "display_order": order,
                },
            )

    def _seed_missions(self):
        for code, title, desc, reward, kind, target, bonus, param, order in sd.MISSIONS:
            Mission.objects.update_or_create(
                code=code,
                defaults={
                    "title": title, "desc": desc, "reward": reward, "kind": kind,
                    "target": target, "bonus_points": bonus, "param": param,
                    "display_order": order,
                },
            )

    def _seed_matches(self, groups, teams):
        count = 0
        for f in normalized_fixtures():
            Match.objects.update_or_create(
                number=f["number"],
                defaults={
                    "slug": f"m{f['number']}",
                    "stage": f["stage"],
                    "group": groups.get(f["group"]) if f["group"] else None,
                    "round_label": f["round_label"],
                    "home_team": teams.get(f["home"]) if f["home"] else None,
                    "away_team": teams.get(f["away"]) if f["away"] else None,
                    "home_placeholder": f["home_placeholder"],
                    "away_placeholder": f["away_placeholder"],
                    "kickoff": parse_datetime(f["kickoff"]),
                    "venue_city": f["venue_city"],
                    "venue_stadium": f["venue_stadium"],
                    "featured": f["featured"],
                },
            )
            count += 1
        self.stdout.write(f"  Partidos sembrados/actualizados: {count}")
