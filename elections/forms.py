from django import forms

from .models import Election, ElectionCandidate, Permission, Role, UserRole


class ElectionCreateForm(forms.ModelForm):
    class Meta:
        model = Election
        fields = [
            "name",
            "description",
            "election_type",
            "election_status",
            "organizational_unit",
            "is_secret",
        ]
        labels = {
            "name": "Nazwa wyborów",
            "description": "Opis",
            "election_type": "Typ wyborów",
            "election_status": "Status wyborów",
            "organizational_unit": "Jednostka organizacyjna",
            "is_secret": "Głosowanie tajne",
        }


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
