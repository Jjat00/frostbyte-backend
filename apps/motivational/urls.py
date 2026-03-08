from django.urls import path
from . import views

urlpatterns = [
    path("phrase/", views.get_motivational_phrase, name="motivational-phrase"),
    path("phrase/8m/", views.get_womens_day_phrase, name="womens-day-phrase"),
    path("recommend/", views.recommend_by_mood, name="motivational-recommend"),
    path("quiz/", views.recommend_by_quiz, name="motivational-quiz"),
    path("transcribe/", views.transcribe_audio, name="motivational-transcribe"),
    path("generate-8m-phrase/", views.generate_8m_phrase, name="generate-8m-phrase"),
    path("generate-8m-image/", views.generate_8m_image, name="generate-8m-image"),
    path("dedications/", views.list_dedications, name="dedications-list"),
    path("dedications/create/", views.create_dedication, name="dedications-create"),
]
