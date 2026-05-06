from django.shortcuts import render
from .models import OrganizationalUnit, Election, ElectionCandidate, ElectionResult
from .forms import ElectionCreateForm, ElectionCandidateCreateForm


def home_view(request):
    return render(request, "elections/home.html")


def candidates_list_view(request):
    candidates = ElectionCandidate.objects.select_related(
        "person",
        "election",
    ).all()

    context = {
        "candidates": candidates,
    }
    return render(request, "elections/candidates/list.html", context)


def committees_list_view(request):
    committees = OrganizationalUnit.objects.filter(is_active=True).all()

    context = {
        "committees": committees,
    }
    return render(request, "elections/committees/list.html", context)


def elections_list_view(request):
    elections = Election.objects.select_related(
        "election_type",
        "election_status",
        "organizational_unit",
    ).all()

    context = {
        "elections": elections,
    }
    return render(request, "elections/elections/list.html", context)


def results_list_view(request):
    results = ElectionResult.objects.select_related(
        "election",
        "generated_by_user",
    ).all()

    context = {
        "results": results,
    }
    return render(request, "elections/results/list.html", context)

def election_create_view(request):
    if request.method == "POST":
        form = ElectionCreateForm(request.POST)
        if form.is_valid():
            election = form.save()
            form = ElectionCreateForm()
            context = {
                "form": form,
                "success_message": f'Dodano wybory: "{election.name}".',
            }
            return render(request, "elections/elections/create.html", context)
    else:
        form = ElectionCreateForm()

    context = {
        "form": form,
    }
    return render(request, "elections/elections/create.html", context)


def candidate_create_view(request):
    if request.method == "POST":
        form = ElectionCandidateCreateForm(request.POST)
        if form.is_valid():
            candidate = form.save()
            form = ElectionCandidateCreateForm()
            context = {
                "form": form,
                "success_message": (
                    f'Dodano kandydaturę: {candidate.person} '
                    f'(wybory: "{candidate.election.name}").'
                ),
            }
            return render(request, "elections/candidates/create.html", context)
    else:
        form = ElectionCandidateCreateForm()

    context = {
        "form": form,
    }
    return render(request, "elections/candidates/create.html", context)
