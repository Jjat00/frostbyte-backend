from django.urls import path
from . import views

urlpatterns = [
    path("phrase/", views.get_motivational_phrase, name="motivational-phrase"),
]
