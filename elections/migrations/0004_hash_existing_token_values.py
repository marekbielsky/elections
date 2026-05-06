import hashlib
from django.conf import settings
from django.db import migrations


HASH_PREFIX = "sha256$"


def _hash_secret(value: str) -> str:
    normalized_value = (value or "").strip()
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}:{normalized_value}".encode("utf-8")
    ).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def _is_hashed_value(value: str) -> bool:
    return bool(value) and value.startswith(HASH_PREFIX)


def hash_existing_secrets(apps, schema_editor):
    VotingToken = apps.get_model("elections", "VotingToken")
    Ballot = apps.get_model("elections", "Ballot")

    for token in VotingToken.objects.all().only("id", "token_value"):
        if token.token_value and not _is_hashed_value(token.token_value):
            token.token_value = _hash_secret(token.token_value)
            token.save(update_fields=["token_value"])

    for ballot in Ballot.objects.all().only("id", "anonymous_key"):
        if ballot.anonymous_key and not _is_hashed_value(ballot.anonymous_key):
            ballot.anonymous_key = _hash_secret(ballot.anonymous_key)
            ballot.save(update_fields=["anonymous_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("elections", "0003_electionevent_election_event_start_before_end_and_more"),
    ]

    operations = [
        migrations.RunPython(hash_existing_secrets, migrations.RunPython.noop),
    ]
