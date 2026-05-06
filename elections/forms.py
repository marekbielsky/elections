from django import forms

from .models import Election, ElectionCandidate


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
