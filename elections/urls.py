from django.urls import path
from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("candidates/", views.candidates_list_view, name="candidates_list"),
    path("candidates/create/", views.candidate_create_view, name="candidate_create"),
    path("committees/", views.committees_list_view, name="committees_list"),
    path("elections/", views.elections_list_view, name="elections_list"),
    path("elections/create/", views.election_create_view, name="election_create"),
    path("results/", views.results_list_view, name="results_list"),
]