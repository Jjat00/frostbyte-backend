from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .email_views import email_unsubscribe
from .views import (
    AwardPickView,
    AwardsView,
    BracketPickView,
    BracketView,
    GranizadoRedeemView,
    GranizadoView,
    GroupsView,
    MatchViewSet,
    MissionsView,
    MyPredictionsView,
    MyStatsView,
    PlayerDetailView,
    PlayerProfileView,
    PollaAdminViewSet,
    RankingView,
    ReferralClaimView,
    ReferralView,
    StandingsView,
    TeamDetailView,
    TopScorersView,
    TournamentView,
)

router = DefaultRouter()
router.register(r"matches", MatchViewSet, basename="polla-match")
router.register(r"admin", PollaAdminViewSet, basename="polla-admin")

urlpatterns = [
    path("tournament/", TournamentView.as_view(), name="polla-tournament"),
    path("groups/", GroupsView.as_view(), name="polla-groups"),
    path("standings/", StandingsView.as_view(), name="polla-standings"),
    path("topscorers/", TopScorersView.as_view(), name="polla-topscorers"),
    path("teams/<str:code>/", TeamDetailView.as_view(), name="polla-team-detail"),
    path("players/<int:pk>/", PlayerDetailView.as_view(), name="polla-player-detail"),
    path("predictions/me/", MyPredictionsView.as_view(), name="polla-my-predictions"),
    path("awards/", AwardsView.as_view(), name="polla-awards"),
    path("awards/<str:code>/pick/", AwardPickView.as_view(), name="polla-award-pick"),
    path("bracket/", BracketView.as_view(), name="polla-bracket"),
    path("bracket/<str:slug>/pick/", BracketPickView.as_view(), name="polla-bracket-pick"),
    path("ranking/", RankingView.as_view(), name="polla-ranking"),
    path("participants/<int:pk>/", PlayerProfileView.as_view(), name="polla-participant"),
    path("missions/", MissionsView.as_view(), name="polla-missions"),
    path("me/stats/", MyStatsView.as_view(), name="polla-my-stats"),
    path("granizado/", GranizadoView.as_view(), name="polla-granizado"),
    path("granizado/<int:pk>/redeem/", GranizadoRedeemView.as_view(), name="polla-granizado-redeem"),
    path("referral/", ReferralView.as_view(), name="polla-referral"),
    path("referral/claim/", ReferralClaimView.as_view(), name="polla-referral-claim"),
    path(
        "email/unsubscribe/<str:token>/",
        email_unsubscribe,
        name="polla-email-unsubscribe",
    ),
    path("", include(router.urls)),
]
