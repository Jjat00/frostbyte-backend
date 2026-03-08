from django.urls import path
from . import views

urlpatterns = [
    path("phrase/", views.get_motivational_phrase, name="motivational-phrase"),
    path("phrase/8m/", views.get_womens_day_phrase, name="womens-day-phrase"),
    path("recommend/", views.recommend_by_mood, name="motivational-recommend"),
    path("quiz/", views.recommend_by_quiz, name="motivational-quiz"),
    path("transcribe/", views.transcribe_audio, name="motivational-transcribe"),
]
