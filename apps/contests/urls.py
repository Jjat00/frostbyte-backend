from django.urls import path

from . import views

urlpatterns = [
    path("current/", views.current_contest, name="contest-current"),
    path("current/entry/", views.my_entry, name="contest-my-entry"),
    path("current/entry/cancel/", views.cancel_my_entry,
         name="contest-my-entry-cancel"),
    path("admin/", views.staff_overview, name="contest-admin"),
    path("admin/contest/", views.staff_update_contest,
         name="contest-admin-contest"),
    path("admin/entries/<int:pk>/", views.staff_update_entry,
         name="contest-admin-entry"),
]
