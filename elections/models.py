import hashlib
import hmac
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import F, Q


class OrganizationalUnit(models.Model):
    name = models.CharField(max_length=150)
    unit_type = models.CharField(max_length=50)
    parent_unit = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_units",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class StoredFile(models.Model):
    original_file_name = models.CharField(max_length=255)
    stored_file_name = models.CharField(max_length=255, unique=True)
    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    mime_type = models.CharField(max_length=100)
    file_size_bytes = models.BigIntegerField()
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_files",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.original_file_name


class Person(models.Model):
    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="person_profile",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=150)
    student_or_employee_no = models.CharField(max_length=50, unique=True)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="people",
    )
    avatar_file = models.ForeignKey(
        StoredFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="person_avatars",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"


class ElectionType(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return self.name


class ElectionStatus(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return self.name


class Election(models.Model):
    election_type = models.ForeignKey(
        ElectionType,
        on_delete=models.PROTECT,
        related_name="elections",
    )
    election_status = models.ForeignKey(
        ElectionStatus,
        on_delete=models.PROTECT,
        related_name="elections",
    )
    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="elections",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_secret = models.BooleanField(default=True)
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_elections",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class ElectionSchedule(models.Model):
    election = models.OneToOneField(
        Election,
        on_delete=models.CASCADE,
        related_name="schedule",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    results_publish_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(start_at__lt=F("end_at")),
                name="election_schedule_start_before_end",
            )
        ]

    def __str__(self) -> str:
        return f"Schedule for {self.election.name}"


class VotingRule(models.Model):
    election = models.OneToOneField(
        Election,
        on_delete=models.CASCADE,
        related_name="voting_rule",
    )
    min_choices = models.IntegerField(default=1, validators=[MinValueValidator(0)])
    max_choices = models.IntegerField(default=1, validators=[MinValueValidator(1)])
    allow_blank_vote = models.BooleanField(default=False)
    allow_vote_change = models.BooleanField(default=False)
    requires_turnout_threshold = models.BooleanField(default=False)
    turnout_threshold_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(max_choices__gte=F("min_choices")),
                name="voting_rule_max_choices_gte_min_choices",
            ),
            models.CheckConstraint(
                condition=Q(requires_turnout_threshold=True)
                | Q(turnout_threshold_percent__isnull=True),
                name="voting_rule_threshold_required_or_null",
            ),
        ]

    def __str__(self) -> str:
        return f"Voting rules for {self.election.name}"


class ElectionCandidate(models.Model):
    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="candidates",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="candidacies",
    )
    avatar_file = models.ForeignKey(
        StoredFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="candidate_avatars",
    )
    candidate_number = models.IntegerField()
    campaign_description = models.TextField(blank=True)
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "person"],
                name="unique_candidate_per_election",
            ),
            models.UniqueConstraint(
                fields=["election", "candidate_number"],
                name="unique_candidate_number_per_election",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} in {self.election}"


class VotingEligibility(models.Model):
    class EligibilityStatus(models.TextChoices):
        GRANTED = "GRANTED", "Granted"
        REVOKED = "REVOKED", "Revoked"
        PENDING = "PENDING", "Pending"

    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="eligibilities",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="voting_eligibilities",
    )
    eligibility_status = models.CharField(
        max_length=30,
        choices=EligibilityStatus.choices,
        default=EligibilityStatus.GRANTED,
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "person"],
                name="unique_eligibility_per_person_and_election",
            )
        ]

    def __str__(self) -> str:
        return f"{self.person} -> {self.election}"


class VotingToken(models.Model):
    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="voting_tokens",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="voting_tokens",
    )
    token_value = models.CharField(max_length=120, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "person"],
                name="unique_token_per_person_and_election",
            )
        ]

    _HASH_PREFIX = "sha256$"

    @classmethod
    def _hash_secret(cls, value: str) -> str:
        normalized_value = (value or "").strip()
        digest = hashlib.sha256(
            f"{settings.SECRET_KEY}:{normalized_value}".encode("utf-8")
        ).hexdigest()
        return f"{cls._HASH_PREFIX}{digest}"

    @classmethod
    def _is_hashed_value(cls, value: str) -> bool:
        return bool(value) and value.startswith(cls._HASH_PREFIX)

    def set_token_value(self, raw_token: str) -> None:
        self.token_value = self._hash_secret(raw_token)

    def verify_token_value(self, raw_token: str) -> bool:
        expected_hash = self._hash_secret(raw_token)
        return hmac.compare_digest(self.token_value or "", expected_hash)

    def save(self, *args, **kwargs):
        if self.token_value and not self._is_hashed_value(self.token_value):
            self.token_value = self._hash_secret(self.token_value)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Token for {self.person} / {self.election}"


class VotingParticipation(models.Model):
    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="participations",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="voting_participations",
    )
    voting_token = models.OneToOneField(
        VotingToken,
        on_delete=models.CASCADE,
        related_name="participation",
    )
    has_voted = models.BooleanField(default=False)
    voted_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "person"],
                name="unique_participation_per_person_and_election",
            )
        ]

    def __str__(self) -> str:
        return f"{self.person} participation in {self.election}"


class Ballot(models.Model):
    class BallotStatus(models.TextChoices):
        CREATED = "CREATED", "Created"
        SUBMITTED = "SUBMITTED", "Submitted"
        CANCELLED = "CANCELLED", "Cancelled"

    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="ballots",
    )
    voting_token = models.OneToOneField(
        VotingToken,
        on_delete=models.CASCADE,
        related_name="ballot",
    )
    anonymous_key = models.CharField(max_length=120, unique=True)
    ballot_status = models.CharField(
        max_length=30,
        choices=BallotStatus.choices,
        default=BallotStatus.CREATED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    _HASH_PREFIX = "sha256$"

    @classmethod
    def _hash_secret(cls, value: str) -> str:
        normalized_value = (value or "").strip()
        digest = hashlib.sha256(
            f"{settings.SECRET_KEY}:{normalized_value}".encode("utf-8")
        ).hexdigest()
        return f"{cls._HASH_PREFIX}{digest}"

    @classmethod
    def _is_hashed_value(cls, value: str) -> bool:
        return bool(value) and value.startswith(cls._HASH_PREFIX)

    def set_anonymous_key(self, raw_key: str) -> None:
        self.anonymous_key = self._hash_secret(raw_key)

    def verify_anonymous_key(self, raw_key: str) -> bool:
        expected_hash = self._hash_secret(raw_key)
        return hmac.compare_digest(self.anonymous_key or "", expected_hash)

    def save(self, *args, **kwargs):
        if self.anonymous_key and not self._is_hashed_value(self.anonymous_key):
            self.anonymous_key = self._hash_secret(self.anonymous_key)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Ballot {self.id} for {self.election}"


class BallotSelection(models.Model):
    ballot = models.ForeignKey(
        Ballot,
        on_delete=models.CASCADE,
        related_name="selections",
    )
    election_candidate = models.ForeignKey(
        ElectionCandidate,
        on_delete=models.CASCADE,
        related_name="ballot_selections",
    )
    selection_order = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ballot", "election_candidate"],
                name="unique_candidate_per_ballot",
            ),
            models.UniqueConstraint(
                fields=["ballot", "selection_order"],
                name="unique_selection_order_per_ballot",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.ballot_id
            and self.election_candidate_id
            and self.ballot.election_id != self.election_candidate.election_id
        ):
            raise ValidationError(
                "Selected candidate must belong to the same election as the ballot."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.ballot} -> {self.election_candidate}"


class ElectionResult(models.Model):
    election = models.OneToOneField(
        Election,
        on_delete=models.CASCADE,
        related_name="result",
    )
    calculated_at = models.DateTimeField(auto_now_add=True)
    eligible_voters_count = models.IntegerField(default=0)
    voters_count = models.IntegerField(default=0)
    turnout_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_final = models.BooleanField(default=False)
    generated_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_results",
    )

    def __str__(self) -> str:
        return f"Result for {self.election}"


class ElectionResultItem(models.Model):
    election_result = models.ForeignKey(
        ElectionResult,
        on_delete=models.CASCADE,
        related_name="items",
    )
    election_candidate = models.ForeignKey(
        ElectionCandidate,
        on_delete=models.CASCADE,
        related_name="result_items",
    )
    votes_count = models.IntegerField(default=0)
    votes_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    ranking_position = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election_result", "election_candidate"],
                name="unique_result_item_per_candidate",
            ),
            models.UniqueConstraint(
                fields=["election_result", "ranking_position"],
                name="unique_ranking_position_per_result",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.election_candidate} - {self.votes_count}"


class GeneratedDocument(models.Model):
    class DocumentType(models.TextChoices):
        RESULT_PDF = "RESULT_PDF", "Result PDF"
        SUMMARY_PDF = "SUMMARY_PDF", "Summary PDF"
        PROTOCOL_PDF = "PROTOCOL_PDF", "Protocol PDF"

    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="generated_documents",
    )
    election_result = models.ForeignKey(
        ElectionResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_documents",
    )
    stored_file = models.ForeignKey(
        StoredFile,
        on_delete=models.CASCADE,
        related_name="generated_documents",
    )
    document_type = models.CharField(max_length=30, choices=DocumentType.choices)
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_documents",
    )

    def __str__(self) -> str:
        return f"{self.document_type} for {self.election}"


class CandidateAttachment(models.Model):
    class AttachmentType(models.TextChoices):
        AVATAR = "AVATAR", "Avatar"
        PROGRAM = "PROGRAM", "Program"
        POSTER = "POSTER", "Poster"
        OTHER = "OTHER", "Other"

    election_candidate = models.ForeignKey(
        ElectionCandidate,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    stored_file = models.ForeignKey(
        StoredFile,
        on_delete=models.CASCADE,
        related_name="candidate_attachments",
    )
    attachment_type = models.CharField(max_length=30, choices=AttachmentType.choices)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.attachment_type} for {self.election_candidate}"


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        SYSTEM = "SYSTEM", "System"
        RESULT = "RESULT", "Result"
        REMINDER = "REMINDER", "Reminder"

    election = models.ForeignKey(
        Election,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient_person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
    )
    subject = models.CharField(max_length=200)
    content = models.TextField()
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.recipient_person}"


class AuditLog(models.Model):
    election = models.ForeignKey(
        Election,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    person = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action_type = models.CharField(max_length=50)
    entity_name = models.CharField(max_length=100)
    entity_id = models.BigIntegerField()
    action_details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.action_type} on {self.entity_name}#{self.entity_id}"


class ElectionEvent(models.Model):
    class EventType(models.TextChoices):
        START = "START", "Start"
        END = "END", "End"
        RESULTS_PUBLICATION = "RESULTS_PUBLICATION", "Results publication"
        MEETING = "MEETING", "Meeting"
        OTHER = "OTHER", "Other"

    election = models.ForeignKey(
        Election,
        on_delete=models.CASCADE,
        related_name="events",
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(start_at__lt=F("end_at")),
                name="election_event_start_before_end",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.election})"