"""Persistent, workspace-scoped support records."""
import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def new_token():
    return secrets.token_urlsafe(32)


class Workspace(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    timezone = models.CharField(max_length=64, default='UTC')
    next_ticket_number = models.PositiveIntegerField(default=1001)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        AGENT = 'agent', 'Support agent'

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.AGENT)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'user'], name='unique_workspace_member')]
        indexes = [models.Index(fields=['workspace', 'is_active'])]

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Customer(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='customers')
    name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    company = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'email'], name='unique_customer_email')]
        indexes = [models.Index(fields=['workspace', 'name'])]
        ordering = ['name']

    def __str__(self):
        return self.name


class Category(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=16, default='#6d5dfc')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'name'], name='unique_workspace_category')]
        ordering = ['name']

    def __str__(self):
        return self.name


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        PENDING = 'PENDING', 'Pending'
        RESOLVED = 'RESOLVED', 'Resolved'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical'

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='tickets')
    number = models.PositiveIntegerField()
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='tickets')
    subject = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.MEDIUM)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    assignee = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_tickets')
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'number'], name='unique_ticket_number'),
            models.CheckConstraint(condition=models.Q(status__in=['OPEN', 'IN_PROGRESS', 'PENDING', 'RESOLVED']), name='valid_ticket_status'),
            models.CheckConstraint(condition=models.Q(priority__in=['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']), name='valid_ticket_priority'),
        ]
        indexes = [models.Index(fields=['workspace', field]) for field in ['status', 'priority', 'assignee', 'customer', 'created_at', 'deleted_at']]
        ordering = ['-created_at', '-pk']

    def clean(self):
        super().clean()
        errors = {}
        for field in ['customer', 'category', 'assignee']:
            related = getattr(self, field, None)
            if related and related.workspace_id != self.workspace_id:
                errors[field] = 'This record belongs to another workspace.'
        if self.assignee_id and not self.assignee.is_active:
            errors['assignee'] = 'Assign tickets to an active workspace member.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'#{self.number} {self.subject}'


class Message(models.Model):
    class Kind(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        AGENT = 'AGENT', 'Agent'
        SYSTEM = 'SYSTEM', 'System'

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='ticket_messages')
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name='messages')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.AGENT)
    internal = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']

    def clean(self):
        if self.customer_id and self.customer.workspace_id != self.ticket.workspace_id:
            raise ValidationError({'customer': 'Customer belongs to another workspace.'})
        if self.author_id and not Membership.objects.filter(workspace_id=self.ticket.workspace_id, user_id=self.author_id, is_active=True).exists():
            raise ValidationError({'author': 'Author must be an active workspace member.'})


class ActivityEvent(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='activities')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='activities')
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.SET_NULL, related_name='activities')
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name='activities')
    kind = models.CharField(max_length=64)
    description = models.CharField(max_length=500)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['workspace', 'created_at'])]

    def clean(self):
        errors = {}
        for field in ['ticket', 'customer']:
            item = getattr(self, field, None)
            if item and item.workspace_id != self.workspace_id:
                errors[field] = 'This record belongs to another workspace.'
        if errors:
            raise ValidationError(errors)


class Notification(models.Model):
    recipient = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name='notifications')
    event = models.ForeignKey(ActivityEvent, on_delete=models.CASCADE, related_name='notifications')
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['recipient', 'event'], name='unique_event_notification')]
        indexes = [models.Index(fields=['recipient', 'read_at'])]

    def clean(self):
        if self.recipient_id and self.event_id and self.recipient.workspace_id != self.event.workspace_id:
            raise ValidationError({'recipient': 'Notification must remain within the event workspace.'})


class SatisfactionResponse(models.Model):
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name='satisfaction')
    score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    token = models.CharField(max_length=64, default=new_token, unique=True, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(score__isnull=True) | models.Q(score__gte=1, score__lte=5), name='valid_satisfaction_score')]


class Invitation(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    role = models.CharField(max_length=12, choices=Membership.Role.choices, default=Membership.Role.AGENT)
    token = models.CharField(max_length=128, unique=True, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AIExecution(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='ai_executions')
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='ai_executions')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='ai_executions')
    operation = models.CharField(max_length=32)
    provider = models.CharField(max_length=64)
    model = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=24)
    duration_ms = models.PositiveIntegerField(default=0)
    source_revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['workspace', 'created_at'])]

    def clean(self):
        if self.ticket_id and self.ticket.workspace_id != self.workspace_id:
            raise ValidationError({'ticket': 'Ticket belongs to another workspace.'})
