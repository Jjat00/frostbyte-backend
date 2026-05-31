"""Puebla la Polla con datos de DEMO para ver la app "viva" antes del torneo.

Crea participantes de ejemplo con pronósticos, marca algunos partidos como
finalizados/en vivo con marcadores de muestra y recalcula el ranking. Útil
para QA y demos; NO usar en producción real.

    python manage.py polla_demo            # poblar
    python manage.py polla_demo --reset    # limpiar datos de demo
    python manage.py polla_demo --user me@gmail.com   # incluir a un usuario real

Los participantes de demo tienen username con prefijo ``demo_`` para poder
limpiarlos sin tocar usuarios reales.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.polla.models import Award, AwardPick, Match, Prediction, Team
from apps.polla.scoring import recompute_all, recompute_missions_for_user

User = get_user_model()

DEMO_NAMES = [
    "Mariana Lasso", "Santiago Leal", "Juan D. Erazo", "Valentina Ruiz",
    "Camilo Chávez", "Daniela Ortega", "Andrés Pantoja", "Laura Méndez",
]

# (número de partido, marcador local, visitante) para marcar como finalizados
DEMO_RESULTS = [
    (1, 2, 1), (2, 0, 1), (3, 1, 1), (4, 0, 2),
    (5, 2, 1), (6, 3, 1), (7, 1, 0), (8, 2, 2),
]
DEMO_LIVE = (11, 1, 0, 38)  # (número, local, visitante, minuto)


class Command(BaseCommand):
    help = "Puebla la Polla con datos de demostración (participantes y resultados)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Limpiar datos de demo.")
        parser.add_argument("--user", type=str, default="", help="Email/username real a incluir.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            return self._reset()

        users = self._ensure_demo_users()
        real = self._resolve_user(options["user"])
        if real:
            users.append(real)

        self._apply_results()
        self._make_predictions(users)
        self._make_award_picks(users)
        recompute_all()
        self.stdout.write(self.style.SUCCESS(
            f"Demo lista: {len(users)} participantes, "
            f"{len(DEMO_RESULTS)} partidos finalizados + 1 en vivo."
        ))

    # -- helpers -----------------------------------------------------------
    def _ensure_demo_users(self):
        users = []
        for i, name in enumerate(DEMO_NAMES):
            first, _, last = name.partition(" ")
            username = f"demo_{i+1}"
            u, created = User.objects.update_or_create(
                username=username,
                defaults={
                    "email": f"{username}@frostbyte.local",
                    "first_name": first, "last_name": last,
                    "role": getattr(User.Role, "CUSTOMER", "customer"),
                },
            )
            if created:
                u.set_unusable_password()
                u.save(update_fields=["password"])
            users.append(u)
        return users

    def _resolve_user(self, ident):
        if not ident:
            return None
        return (
            User.objects.filter(email__iexact=ident).first()
            or User.objects.filter(username=ident).first()
        )

    def _apply_results(self):
        for number, h, a in DEMO_RESULTS:
            Match.objects.filter(number=number).update(
                status=Match.Status.FINISHED, home_score=h, away_score=a, minute=None
            )
        n, h, a, minute = DEMO_LIVE
        Match.objects.filter(number=n).update(
            status=Match.Status.LIVE, home_score=h, away_score=a, minute=minute
        )

    def _make_predictions(self, users):
        finished = list(Match.objects.filter(status=Match.Status.FINISHED).order_by("number"))
        upcoming = list(
            Match.objects.filter(status=Match.Status.UPCOMING).order_by("kickoff")[:10]
        )
        for i, user in enumerate(users):
            # Sobre partidos finalizados: variar para generar exactos/aciertos/fallos
            for m in finished:
                mode = (i + m.number) % 3
                if mode == 0:          # exacto
                    ph, pa = m.home_score, m.away_score
                elif mode == 1:        # mismo resultado, marcador distinto
                    ph, pa = m.home_score + 1, m.away_score + 1
                else:                  # invertido (probable fallo)
                    ph, pa = m.away_score, m.home_score
                Prediction.objects.update_or_create(
                    user=user, match=m, defaults={"home_score": ph, "away_score": pa}
                )
            # Algunos pronósticos sobre próximos partidos
            for m in upcoming[: 5 + (i % 4)]:
                Prediction.objects.update_or_create(
                    user=user, match=m,
                    defaults={"home_score": (i % 3), "away_score": ((i + 1) % 3)},
                )

    def _make_award_picks(self, users):
        champ = Award.objects.filter(code="champion").first()
        teams = list(Team.objects.order_by("name"))
        if champ and teams:
            for i, user in enumerate(users):
                AwardPick.objects.update_or_create(
                    user=user, award=champ,
                    defaults={"team": teams[i % len(teams)], "player": None},
                )

    def _reset(self):
        demo = User.objects.filter(username__startswith="demo_")
        Prediction.objects.filter(user__in=demo).delete()
        AwardPick.objects.filter(user__in=demo).delete()
        count = demo.count()
        demo.delete()
        # Devolver los partidos de demo a estado "próximo"
        nums = [n for n, *_ in DEMO_RESULTS] + [DEMO_LIVE[0]]
        Match.objects.filter(number__in=nums).update(
            status=Match.Status.UPCOMING, home_score=None, away_score=None, minute=None
        )
        recompute_all()
        self.stdout.write(self.style.SUCCESS(f"Demo limpiada: {count} participantes eliminados."))
