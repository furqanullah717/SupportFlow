"""Create a repeatable, explicitly requested demonstration workspace."""
from datetime import timedelta
from random import Random
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from desk.models import (
    ActivityEvent, Category, Customer, Membership, Message, Notification,
    SatisfactionResponse, Ticket, Workspace,
)


class Command(BaseCommand):
    help = 'Create the isolated SupportFlow demonstration workspace. Never runs automatically.'

    @transaction.atomic
    def handle(self, *args, **options):
        workspace, created = Workspace.objects.get_or_create(
            slug='supportflow-demo', defaults={'name': 'Acme Studio', 'next_ticket_number': 1101},
        )
        if not created:
            self.stdout.write('Demo workspace already exists; preserving all existing data.')
            return
        rng = Random(42)
        now = timezone.now()
        members = []
        people = [
            ('admin@supportflow.demo', 'Alex', 'Morgan', 'admin'),
            ('agent@supportflow.demo', 'Jamie', 'Chen', 'agent'),
            ('sarah@supportflow.demo', 'Sarah', 'Wilson', 'agent'),
            ('marcus@supportflow.demo', 'Marcus', 'Reed', 'agent'),
            ('priya@supportflow.demo', 'Priya', 'Sharma', 'agent'),
            ('leo@supportflow.demo', 'Leo', 'Martinez', 'agent'),
        ]
        for email, first, last, role in people:
            user, new = User.objects.get_or_create(username=email, defaults={'email': email, 'first_name': first, 'last_name': last})
            if not new:
                raise CommandError(f'Demo account {email} already exists. Refusing to reuse an existing account.')
            user.set_password('SupportFlow!2026')
            user.save()
            members.append(Membership.objects.create(workspace=workspace, user=user, role=role))
        categories = {name: Category.objects.create(workspace=workspace, name=name, color=color) for name, color in [
            ('Billing', '#b97730'), ('Technical issue', '#635bdb'), ('Account access', '#317fb0'),
            ('Feature request', '#3c937d'), ('Getting started', '#bc5d83'),
        ]}
        names = [
            ('Olivia', 'Rhodes', 'Linear Labs'), ('Ethan', 'Park', 'Atlas Ventures'),
            ('Emma', 'Thompson', 'Northstar'), ('Liam', 'Patel', 'Brightside'),
            ('Sophia', 'Garcia', 'Forma Design'), ('Noah', 'Williams', 'Notion Works'),
            ('Isabella', 'Kim', 'Orbit Health'), ('James', 'Brown', 'Bloom Commerce'),
            ('Mia', 'Davis', 'Arc Software'), ('Lucas', 'Nguyen', 'Finch Studio'),
            ('Amelia', 'Taylor', 'Sprout'), ('Benjamin', 'Lee', 'Evergreen'),
            ('Harper', 'Anderson', 'Fable'), ('Henry', 'Thomas', 'Fieldwork'),
            ('Evelyn', 'Jackson', 'Avenue'), ('Alexander', 'White', 'Cobalt'),
            ('Abigail', 'Harris', 'Waypoint'), ('Daniel', 'Martin', 'Hearth'),
            ('Ella', 'Clark', 'Meridian'), ('Michael', 'Lewis', 'Crescent'),
            ('Scarlett', 'Robinson', 'Sunday Studio'), ('Sebastian', 'Walker', 'Kindred'),
            ('Grace', 'Young', 'Daylight'), ('Jack', 'Allen', 'Basis'), ('Chloe', 'King', 'Mosaic'),
        ]
        customers = []
        for i, (first, last, company) in enumerate(names):
            customer = Customer.objects.create(
                workspace=workspace, name=f'{first} {last}', email=f'{first.lower()}.{last.lower()}@example.com',
                phone=f'+1 415 555 {1000+i:04}', company=company,
                notes=f'{company} uses our team plan. Preferred contact: email.',
            )
            Customer.objects.filter(pk=customer.pk).update(created_at=now-timedelta(days=90+i))
            customers.append(customer)
        topics = [
            ('Unable to export monthly reports', 'Technical issue', 'Our CSV export stops at 80% and never completes. We need last month’s report for our team review. Could you help us investigate?'),
            ('Question about our latest invoice', 'Billing', 'The latest invoice includes an additional seat charge. We removed that member last week. Could you check the billing period and clarify the amount?'),
            ('Need help connecting our workspace', 'Getting started', 'We are setting up our new workspace and would like help importing our customer list. Is there a recommended CSV format?'),
            ('Two-factor authentication reset', 'Account access', 'I replaced my phone and can no longer access my authentication codes. Please help me recover access to my account.'),
            ('Add custom fields to customer profiles', 'Feature request', 'Our team would love to track subscription tier and renewal date on customer profiles. Is this on your roadmap?'),
            ('Webhook deliveries failing intermittently', 'Technical issue', 'Several webhook events returned 502 responses this morning. Our endpoint is healthy now. Can we replay the failed events?'),
            ('Upgrade to an annual subscription', 'Billing', 'We would like to switch our team to annual billing. Please confirm how the remaining monthly credit will be applied.'),
            ('Invite email has not arrived', 'Account access', 'My teammate has not received their invitation after two attempts. We checked the spam folder. Their address is correct.'),
            ('Dashboard data is slow to refresh', 'Technical issue', 'Our activity dashboard takes several minutes to show new updates. We first noticed this after adding a second integration.'),
            ('How do we organize multiple teams?', 'Getting started', 'We are adding a second support team next week. Can you recommend a setup that keeps their assignments separate?'),
            ('Request for a dark mode option', 'Feature request', 'Our team works across several time zones and would appreciate a dark appearance setting for evening shifts.'),
            ('Payment method update failed', 'Billing', 'I tried to update our company card but received an unexpected error. The card is valid and supports recurring payments.'),
            ('API rate limit clarification', 'Technical issue', 'We are building an integration and want to confirm the request limits and recommended retry behavior before launch.'),
            ('Permission to manage team members', 'Account access', 'A team lead needs access to invite members and manage assignments. Which role should we use for this?'),
            ('Help with onboarding checklist', 'Getting started', 'Our team is ready to launch. Could you review the recommended setup steps for notifications and customer profiles?'),
            ('Duplicate charge on company card', 'Billing', 'Our accounting team noticed two charges with the same amount. Could you confirm whether the second transaction is only an authorization?'),
        ]
        for i in range(100):
            subject, category_name, description = topics[i % len(topics)]
            customer = customers[(i * 7) % len(customers)]
            member = members[(i % 5) + 1]
            # First dozen records form an active queue; historical work spans two months.
            age = timedelta(hours=i * 2 + 1) if i < 12 else timedelta(days=(i-12) % 59 + 1, hours=i % 12)
            created_at = now - age
            if i < 12:
                status = ['OPEN', 'IN_PROGRESS', 'PENDING'][i % 3]
            else:
                status = 'RESOLVED' if i % 5 else ['OPEN', 'PENDING', 'IN_PROGRESS'][i % 3]
            priority = ['MEDIUM', 'HIGH', 'LOW', 'MEDIUM', 'CRITICAL', 'MEDIUM', 'LOW'][i % 7]
            first_response_at = created_at + timedelta(minutes=rng.randint(8, 115)) if status != 'OPEN' else None
            resolved_at = min(created_at + timedelta(hours=rng.randint(3, 18)), now) if status == 'RESOLVED' else None
            last_update = resolved_at or first_response_at or created_at
            ticket = Ticket.objects.create(
                workspace=workspace, number=1001+i, customer=customer, subject=subject,
                description=description, status=status, priority=priority, category=categories[category_name],
                assignee=member if i % 9 else None, created_by=members[0].user,
                first_response_at=first_response_at, resolved_at=resolved_at,
            )
            Ticket.objects.filter(pk=ticket.pk).update(created_at=created_at, updated_at=last_update)
            incoming = Message.objects.create(ticket=ticket, customer=customer, kind='CUSTOMER', body=f'Hi team,\n\n{description}\n\nThanks,\n{customer.name.split()[0]}')
            Message.objects.filter(pk=incoming.pk).update(created_at=created_at)
            if first_response_at:
                reply = Message.objects.create(ticket=ticket, author=member.user, kind='AGENT', body=f'Hi {customer.name.split()[0]},\n\nThanks for reaching out. I have reviewed the details and am checking this with our {"billing" if category_name == "Billing" else "support"} team. I will follow up here with the next steps.\n\nBest,\n{member.user.first_name}')
                Message.objects.filter(pk=reply.pk).update(created_at=first_response_at)
            if i % 3 == 0:
                note = Message.objects.create(ticket=ticket, author=member.user, kind='AGENT', internal=True, body='Reviewed the customer history. Please keep this thread assigned to our team and follow up after the next check.')
                Message.objects.filter(pk=note.pk).update(created_at=last_update)
            if resolved_at:
                reply = Message.objects.create(ticket=ticket, author=member.user, kind='AGENT', body=f'Hi {customer.name.split()[0]},\n\nThis is now resolved. We have verified the account settings and confirmed everything is working. Please reply here if you need anything else.\n\nBest,\n{member.user.first_name}')
                Message.objects.filter(pk=reply.pk).update(created_at=resolved_at)
                if i % 3:
                    score = [5, 5, 4, 5, 4, 5, 3, 5, 4, 2][i % 10]
                    SatisfactionResponse.objects.create(ticket=ticket, score=score, comment='Thank you for the clear explanation and quick help.' if score >= 4 else 'The issue is fixed, but the response took longer than expected.', submitted_at=min(resolved_at+timedelta(hours=2), now))
                else:
                    SatisfactionResponse.objects.create(ticket=ticket)
            event = ActivityEvent.objects.create(workspace=workspace, actor=member.user, ticket=ticket, customer=customer, kind='ticket.resolved' if status == 'RESOLVED' else 'ticket.created', description=f'{member.user.get_full_name()} {"resolved" if status == "RESOLVED" else "updated"} ticket #{ticket.number}', changes={'status': status})
            ActivityEvent.objects.filter(pk=event.pk).update(created_at=last_update)
            if i < 8:
                notification = Notification.objects.create(recipient=members[0], event=event, read_at=now if i > 4 else None)
                Notification.objects.filter(pk=notification.pk).update(created_at=last_update)
        self.stdout.write(self.style.SUCCESS('Created demo workspace: 100 tickets, 25 customers, 6 members. Login: admin@supportflow.demo / SupportFlow!2026'))
