from django.urls import path
from . import views
from .celebration_cards import (
    generate_celebration_card,
    celebration_card_stats,
    suggest_celebration_phrase,
)

urlpatterns = [
    path("celebration-card/", generate_celebration_card, name="celebration-card"),
    path("celebration-card/stats/", celebration_card_stats, name="celebration-card-stats"),
    path("celebration-card/phrase/", suggest_celebration_phrase, name="celebration-card-phrase"),
    path("phrase/", views.get_motivational_phrase, name="motivational-phrase"),
    path("recommend/", views.recommend_by_mood, name="motivational-recommend"),
    path("quiz/", views.recommend_by_quiz, name="motivational-quiz"),
    path("transcribe/", views.transcribe_audio, name="motivational-transcribe"),
]
