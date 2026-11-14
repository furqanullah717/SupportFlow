"""Transactional business operations shared by page and API handlers."""
import hashlib
import secrets
from datetime import timedelta
from django.contrib.auth.models import User
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from .models import ActivityEvent, Category, Customer, Invitation, Membership, Message, Notification, SatisfactionResponse, Ticket, Workspace


class DomainError(Exception):
    def __init__(self, message, code='invalid', status=400, fields=None):
        self.message, self.code, self.status, self.fields = message, code, status, fields
        super().__init__(message)


def feedback_token_for(ticket_id):
    return signing.Signer(salt='feedback').sign(str(ticket_id))


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def record_event(workspace, actor, kind, description, ticket=None, customer=None, changes=None):
    event = ActivityEvent(workspace=workspace, actor=actor, kind=kind, description=description, ticket=ticket, customer=customer, changes=changes or {})
    event.full_clean()
    event.save()
    recipients = workspace.memberships.filter(is_active=True).exclude(user=actor)
    recipients = recipients.filter(role='admin') | recipients.filter(pk=ticket.assignee_id if ticket else None)
    for member in recipients.distinct():
        Notification.objects.get_or_create(recipient=member, event=event)
    return event


def require_admin(member):
    if member.role != 'admin':
        raise DomainError('An administrator is required for this action.', 'forbidden', 403)


def check_revision(ticket, revision):
    try:
        valid = int(revision) == ticket.revision
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise DomainError('This ticket has changed. Refresh the page and try again.', 'conflict', 409)


def ensure_survey(ticket):
    if ticket.status == Ticket.Status.RESOLVED:
        SatisfactionResponse.objects.get_or_create(ticket=ticket, defaults={'token': token_digest(feedback_token_for(ticket.pk))})


@transaction.atomic
def create_ticket(member, data):
    workspace = Workspace.objects.select_for_update().get(pk=member.workspace_id)
    ticket = Ticket(workspace=workspace, number=workspace.next_ticket_number, created_by=member.user, **{k: v for k, v in data.items() if k in ['customer','subject','description','status','priority','assignee','category']})
    if ticket.status == Ticket.Status.RESOLVED:
        ticket.resolved_at = timezone.now()
    ticket.full_clean()
    ticket.save()
    workspace.next_ticket_number += 1
    workspace.save(update_fields=['next_ticket_number'])
    ensure_survey(ticket)
    record_event(workspace, member.user, 'ticket.created', f'created ticket SF-{ticket.number}', ticket=ticket, customer=ticket.customer)
    return ticket


@transaction.atomic
def update_ticket(member, ticket_id, data):
    ticket = Ticket.objects.select_for_update(of=('self',)).select_related('customer','assignee').get(pk=ticket_id, workspace=member.workspace, deleted_at__isnull=True)
    check_revision(ticket, data.get('revision'))
    changes = {}
    previous_status = ticket.status
    for key in ['customer','subject','description','status','priority','assignee','category']:
        if key in data:
            old, value = getattr(ticket, key), data[key]
            if old != value:
                changes[key] = {'from': str(old or ''), 'to': str(value or '')}
                setattr(ticket, key, value)
    if ticket.status != previous_status:
        ticket.resolved_at = timezone.now() if ticket.status == Ticket.Status.RESOLVED else None
    ticket.revision += 1
    ticket.full_clean()
    ticket.save()
    ensure_survey(ticket)
    record_event(member.workspace, member.user, 'ticket.updated', f'updated ticket SF-{ticket.number}', ticket=ticket, customer=ticket.customer, changes=changes)
    return ticket


@transaction.atomic
def delete_ticket(member, ticket_id, revision):
    require_admin(member)
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, workspace=member.workspace, deleted_at__isnull=True)
    check_revision(ticket, revision)
    ticket.deleted_at = timezone.now()
    ticket.revision += 1
    ticket.save(update_fields=['deleted_at','revision','updated_at'])
    record_event(member.workspace, member.user, 'ticket.deleted', f'deleted ticket SF-{ticket.number}', ticket=ticket, customer=ticket.customer)


@transaction.atomic
def add_message(member, ticket_id, data):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id, workspace=member.workspace, deleted_at__isnull=True)
    check_revision(ticket, data.get('revision'))
    message = Message(ticket=ticket, author=member.user, kind=Message.Kind.AGENT, internal=data.get('internal', False), body=data['body'])
    message.full_clean()
    message.save()
    if not message.internal and ticket.first_response_at is None:
        ticket.first_response_at = message.created_at
    ticket.revision += 1
    ticket.full_clean()
    ticket.save()
    record_event(member.workspace, member.user, 'ticket.note' if message.internal else 'ticket.reply', f'{"added an internal note to" if message.internal else "replied to"} SF-{ticket.number}', ticket=ticket, customer=ticket.customer)
    return message


@transaction.atomic
def save_customer(member, form):
    existing = bool(form.instance.pk)
    customer = form.save(commit=False)
    customer.workspace = member.workspace
    customer.full_clean()
    customer.save()
    record_event(member.workspace, member.user, 'customer.updated' if existing else 'customer.created', f'{"updated" if existing else "created"} customer {customer.name}', customer=customer)
    return customer


@transaction.atomic
def create_invitation(member, data):
    require_admin(member)
    email = data['email'].lower()
    if Membership.objects.filter(workspace=member.workspace, is_active=True, user__email__iexact=email).exists():
        raise DomainError('This person is already an active member.')
    raw = secrets.token_urlsafe(32)
    invite = Invitation(workspace=member.workspace, email=email, role=data['role'], token=token_digest(raw), expires_at=timezone.now()+timedelta(days=7))
    invite.full_clean()
    invite.save()
    record_event(member.workspace, member.user, 'team.invited', f'invited {email} as {invite.get_role_display()}')
    return raw


@transaction.atomic
def update_member(actor, target_id, data):
    require_admin(actor)
    Workspace.objects.select_for_update().get(pk=actor.workspace_id)
    target = Membership.objects.select_for_update().get(pk=target_id, workspace=actor.workspace)
    if target.is_active and target.role == 'admin' and (data['role'] != 'admin' or not data['is_active']):
        if not Membership.objects.filter(workspace=actor.workspace, role='admin', is_active=True).exclude(pk=target.pk).exists():
            raise DomainError('The workspace must keep at least one active administrator.')
    target.role, target.is_active = data['role'], data['is_active']
    target.full_clean()
    target.save()
    if not target.is_active:
        Ticket.objects.filter(workspace=actor.workspace, assignee=target, deleted_at__isnull=True).update(assignee=None, revision=models_f_revision())
    record_event(actor.workspace, actor.user, 'team.updated', f'updated team access for {target}')
    return target


def models_f_revision():
    from django.db.models import F
    return F('revision') + 1


@transaction.atomic
def register_workspace(data):
    if User.objects.filter(email__iexact=data['email']).exists() or User.objects.filter(username__iexact=data['email']).exists():
        raise DomainError('An account with this email already exists.')
    first, _, last = data['name'].strip().partition(' ')
    user = User.objects.create_user(username=data['email'], email=data['email'], password=data['password1'], first_name=first, last_name=last)
    slug = f'{slugify(data["workspace_name"])[:40] or "workspace"}-{secrets.token_hex(4)}'
    workspace = Workspace.objects.create(name=data['workspace_name'], slug=slug)
    Membership.objects.create(workspace=workspace,user=user,role='admin')
    for name in ['Billing','Technical issue','Account access','Feature request','Getting started']:
        Category.objects.create(workspace=workspace,name=name)
    record_event(workspace,user,'workspace.created','created the workspace')
    return user, workspace


@transaction.atomic
def accept_invitation(raw, user, data=None):
    try:
        invite = Invitation.objects.select_for_update().select_related('workspace').get(token=token_digest(raw), accepted_at__isnull=True, expires_at__gt=timezone.now())
    except Invitation.DoesNotExist:
        raise DomainError('This invitation has expired or has already been used.', 'invalid_invitation', 404)
    if user.is_authenticated:
        if user.email.lower() != invite.email.lower():
            raise DomainError('Sign in with the account matching this invitation email.', 'forbidden', 403)
    else:
        if User.objects.filter(email__iexact=invite.email).exists() or User.objects.filter(username__iexact=invite.email).exists():
            raise DomainError('Sign in with your existing account to accept this invitation.', 'forbidden', 403)
        if not data:
            raise DomainError('Create your account to accept this invitation.')
        first, _, last = data['name'].partition(' ')
        user = User.objects.create_user(username=invite.email,email=invite.email,password=data['password1'],first_name=first,last_name=last)
    membership, _ = Membership.objects.get_or_create(workspace=invite.workspace,user=user,defaults={'role':invite.role})
    if not membership.is_active:
        membership.is_active, membership.role = True, invite.role
        membership.save()
    invite.accepted_at = timezone.now()
    invite.save(update_fields=['accepted_at'])
    record_event(invite.workspace,user,'team.joined',f'{user.get_full_name() or user.email} joined the workspace')
    return user, invite.workspace
