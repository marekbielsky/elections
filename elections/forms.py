from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import (
    ElectionCandidate,
    ElectionStatus,
    ElectionType,
    OrganizationalUnit,
    Permission,
    Role,
    UserRole,
)
from .services import ElectionLifecycleService

class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True, label="E-mail")

    class Meta:
        model = get_user_model()
        fields = ("username", "email", "password1", "password2")


class UserLoginForm(AuthenticationForm):
    username = forms.CharField(label="Nazwa użytkownika")
    password = forms.CharField(label="Hasło", widget=forms.PasswordInput)


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
