from django.urls import path
from . import views

urlpatterns = [
    path("phrase/", views.get_motivational_phrase, name="motivational-phrase"),
    path("phrase/dia-madre/", views.get_mothers_day_phrase, name="mothers-day-phrase"),
    path("recommend/", views.recommend_by_mood, name="motivational-recommend"),
    path("quiz/", views.recommend_by_quiz, name="motivational-quiz"),
    path("transcribe/", views.transcribe_audio, name="motivational-transcribe"),
    path(
        "generate-mothers-day-phrase/",
        views.generate_mothers_day_phrase,
        name="generate-mothers-day-phrase",
    ),
    path(
        "generate-mothers-day-image/",
        views.generate_mothers_day_image,
        name="generate-mothers-day-image",
    ),
    path(
        "mothers-day-track/",
        views.track_mothers_day_event,
        name="mothers-day-track",
    ),
    path(
        "mother-dedications/",
        views.list_mother_dedications,
        name="mother-dedications-list",
    ),
    path(
        "mother-dedications/create/",
        views.create_mother_dedication,
        name="mother-dedications-create",
    ),
]
