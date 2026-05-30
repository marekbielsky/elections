from django import forms
from captcha.fields import CaptchaField
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils import timezone

from .models import (
    Election,
    ElectionCandidate,
    ElectionStatus,
    ElectionType,
    OrganizationalUnit,
    Permission,
    Person,
    Role,
    UserRole,
    VotingEligibility,
)
from .services import ElectionLifecycleService

class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True, label="E-mail")
    captcha = CaptchaField(label="Przepisz kod z obrazka")
    error_messages = {
        "password_mismatch": "Podane hasła nie są identyczne.",
    }

    class Meta:
        model = get_user_model()
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Nazwa użytkownika"
        self.fields["email"].label = "E-mail"
        self.fields["password1"].label = "Hasło"
        self.fields["password2"].label = "Powtórz hasło"
        self.fields["captcha"].help_text = "Wpisz znaki widoczne na obrazku."


class UserLoginForm(AuthenticationForm):
    username = forms.CharField(label="Nazwa użytkownika")
    password = forms.CharField(label="Hasło", widget=forms.PasswordInput)
    error_messages = {
        "invalid_login": "Wprowadź poprawną nazwę użytkownika i hasło.",
        "inactive": "To konto jest nieaktywne.",
    }


class ElectionCreateForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nazwa wyborów")
    description = forms.CharField(required=False, widget=forms.Textarea, label="Opis")
    election_type = forms.ModelChoiceField(
        queryset=ElectionType.objects.order_by("name"),
        label="Typ wyborów",
    )
    election_status = forms.ModelChoiceField(
        queryset=ElectionStatus.objects.order_by("name"),
        label="Status wyborów",
    )
    organizational_unit = forms.ModelChoiceField(
        queryset=OrganizationalUnit.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Jednostka organizacyjna",
    )
    is_secret = forms.BooleanField(required=False, initial=True, label="Głosowanie tajne")
    start_at = forms.DateTimeField(
        label="Start wyborów",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    end_at = forms.DateTimeField(
        label="Koniec wyborów",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    results_publish_at = forms.DateTimeField(
        required=False,
        label="Publikacja wyników",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    min_choices = forms.IntegerField(min_value=0, initial=1, label="Minimalna liczba wyborów")
    max_choices = forms.IntegerField(min_value=1, initial=1, label="Maksymalna liczba wyborów")
    allow_blank_vote = forms.BooleanField(required=False, label="Dopuść pusty głos")
    allow_vote_change = forms.BooleanField(required=False, label="Dopuść zmianę głosu")
    eligible_people = forms.ModelMultipleChoiceField(
        queryset=Person.objects.select_related("user").order_by("last_name", "first_name"),
        required=False,
        label="Osoby uprawnione do głosowania",
        help_text="Opcjonalnie wybierz osoby, które mogą oddać głos w tych wyborach.",
    )

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get("start_at")
        end_at = cleaned_data.get("end_at")
        min_choices = cleaned_data.get("min_choices")
        max_choices = cleaned_data.get("max_choices")
        if start_at and end_at and start_at >= end_at:
            self.add_error("end_at", "Data końca musi być późniejsza niż data startu.")
        if min_choices is not None and max_choices is not None and min_choices > max_choices:
            self.add_error("max_choices", "Maksymalna liczba wyborów nie może być mniejsza niż minimalna.")
        return cleaned_data

    def save(self, *, created_by_user=None):
        data = self.cleaned_data
        eligible_people = list(data.get("eligible_people") or [])
        election = ElectionLifecycleService.create_election_with_config(
            election_type=data["election_type"],
            name=data["name"],
            election_status=data["election_status"],
            start_at=data["start_at"],
            end_at=data["end_at"],
            created_by_user=created_by_user,
            organizational_unit=data.get("organizational_unit"),
            description=data.get("description") or "",
            is_secret=data.get("is_secret", True),
            min_choices=data["min_choices"],
            max_choices=data["max_choices"],
            allow_blank_vote=data.get("allow_blank_vote", False),
            allow_vote_change=data.get("allow_vote_change", False),
        )
        if data.get("results_publish_at"):
            election.schedule.results_publish_at = data["results_publish_at"]
            election.schedule.save(update_fields=["results_publish_at"])
        if eligible_people:
            VotingEligibility.objects.bulk_create(
                [
                    VotingEligibility(
                        election=election,
                        person=person,
                        eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
                    )
                    for person in eligible_people
                ],
                ignore_conflicts=True,
            )
        return election


class ElectionCandidateCreateForm(forms.ModelForm):
    class Meta:
        model = ElectionCandidate
        fields = [
            "election",
            "person",
            "candidate_number",
            "campaign_description",
            "is_approved",
        ]
        labels = {
            "election": "Wybory",
            "person": "Osoba",
            "candidate_number": "Numer kandydata",
            "campaign_description": "Opis kampanii",
            "is_approved": "Kandydatura zatwierdzona",
        }


class UserRoleAssignmentForm(forms.Form):
    user_id = forms.IntegerField(widget=forms.HiddenInput())
    role = forms.ChoiceField(
        choices=UserRole.Role.choices,
        label="Rola użytkownika",
    )


class RolePermissionAssignmentForm(forms.Form):
    role_id = forms.ModelChoiceField(
        queryset=Role.objects.order_by("code"),
        to_field_name="id",
        label="Rola",
    )
    permission_id = forms.ModelChoiceField(
        queryset=Permission.objects.order_by("code"),
        to_field_name="id",
        label="Uprawnienie",
    )
    grant = forms.BooleanField(
        required=False,
        initial=True,
        label="Nadaj uprawnienie (odznacz, aby odebrać)",
    )


class ElectionLifecycleActionForm(forms.Form):
    election_id = forms.IntegerField(widget=forms.HiddenInput())
    action = forms.ChoiceField(
        choices=(
            ("publish", "Opublikuj"),
            ("start", "Rozpocznij"),
            ("close", "Zamknij"),
        ),
        label="Akcja",
    )
    force_close = forms.BooleanField(
        required=False,
        label="Wymuś zamknięcie (przed końcem harmonogramu)",
    )


class CastVoteForm(forms.Form):
    election = forms.ModelChoiceField(
        queryset=Election.objects.select_related("election_status").order_by("name"),
        label="Wybory",
    )
    candidate_ids = forms.MultipleChoiceField(
        required=False,
        label="Kandydaci",
        widget=forms.CheckboxSelectMultiple,
    )
    anonymous_key = forms.CharField(
        max_length=120,
        required=False,
        label="Klucz anonimowy (opcjonalnie)",
        help_text="Jeśli puste, system wygeneruje go automatycznie.",
    )

    def __init__(self, *args, **kwargs):
        person = kwargs.pop("person", None)
        super().__init__(*args, **kwargs)
        if person is not None:
            now = timezone.now()
            self.fields["election"].queryset = (
                Election.objects.select_related("election_status", "schedule")
                .filter(
                    election_status__code="IN_PROGRESS",
                    schedule__start_at__lte=now,
                    schedule__end_at__gte=now,
                    eligibilities__person=person,
                    eligibilities__eligibility_status=VotingEligibility.EligibilityStatus.GRANTED,
                )
                .distinct()
                .order_by("name")
            )
        else:
            self.fields["election"].queryset = Election.objects.none()
        selected_election_id = None
        if self.is_bound:
            selected_election_id = self.data.get("election")
        else:
            initial_election = self.initial.get("election") if self.initial else None
            if initial_election is not None:
                selected_election_id = getattr(initial_election, "id", initial_election)
        self.fields["candidate_ids"].choices = self._candidate_choices(selected_election_id)

    @staticmethod
    def _candidate_choices(election_id):
        if not election_id:
            return []
        try:
            election_id_int = int(election_id)
        except (TypeError, ValueError):
            return []
        candidates = (
            ElectionCandidate.objects.select_related("person")
            .filter(election_id=election_id_int, is_approved=True)
            .order_by("candidate_number", "person__last_name", "person__first_name")
        )
        return [
            (
                str(candidate.id),
                f"{candidate.candidate_number}. {candidate.person.first_name} {candidate.person.last_name}",
            )
            for candidate in candidates
        ]
