from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AwardPickView,
    AwardsView,
    BracketPickView,
    BracketView,
    GroupsView,
    MatchViewSet,
    MissionsView,
    MyPredictionsView,
    MyStatsView,
    RankingView,
    ReferralClaimView,
    ReferralView,
    StandingsView,
    TournamentView,
)

router = DefaultRouter()
router.register(r"matches", MatchViewSet, basename="polla-match")

urlpatterns = [
    path("tournament/", TournamentView.as_view(), name="polla-tournament"),
    path("groups/", GroupsView.as_view(), name="polla-groups"),
    path("standings/", StandingsView.as_view(), name="polla-standings"),
    path("predictions/me/", MyPredictionsView.as_view(), name="polla-my-predictions"),
    path("awards/", AwardsView.as_view(), name="polla-awards"),
    path("awards/<str:code>/pick/", AwardPickView.as_view(), name="polla-award-pick"),
    path("bracket/", BracketView.as_view(), name="polla-bracket"),
    path("bracket/<str:slug>/pick/", BracketPickView.as_view(), name="polla-bracket-pick"),
    path("ranking/", RankingView.as_view(), name="polla-ranking"),
    path("missions/", MissionsView.as_view(), name="polla-missions"),
    path("me/stats/", MyStatsView.as_view(), name="polla-my-stats"),
    path("referral/", ReferralView.as_view(), name="polla-referral"),
    path("referral/claim/", ReferralClaimView.as_view(), name="polla-referral-claim"),
    path("", include(router.urls)),
]
