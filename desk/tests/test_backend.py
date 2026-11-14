from django.contrib.auth.models import User
from django.test import TestCase
from desk.models import Workspace,Membership,Customer,Ticket,Message,Notification

class BackendTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('owner@example.com',email='owner@example.com',password='StrongPass!2026')
        self.workspace=Workspace.objects.create(name='Test',slug='test')
        self.member=Membership.objects.create(workspace=self.workspace,user=self.user,role='admin')
        self.customer=Customer.objects.create(workspace=self.workspace,name='Customer',email='customer@example.com')
        self.client.force_login(self.user)

    def create_ticket(self):
        response=self.client.post('/api/v1/tickets/',{'customer':self.customer.pk,'subject':'Please help','description':'A question','status':'OPEN','priority':'MEDIUM'},content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        return response.json()

    def test_ticket_api_persists_created_record(self):
        data=self.create_ticket()
        ticket=Ticket.objects.get(pk=data['id'])
        self.assertEqual(ticket.subject,'Please help')
        self.assertEqual(ticket.activities.count(),1)

    def test_unauthenticated_api_is_401_and_pages_redirect(self):
        self.client.logout()
        self.assertEqual(self.client.get('/api/v1/tickets/').status_code,401)
        self.assertEqual(self.client.get('/').status_code,302)

    def test_workspace_isolation_and_inactive_membership(self):
        other=Workspace.objects.create(name='Other',slug='other')
        customer=Customer.objects.create(workspace=other,name='Other customer',email='other@example.com')
        ticket=Ticket.objects.create(workspace=other,customer=customer,subject='Private',number=1001)
        self.assertEqual(self.client.get(f'/api/v1/tickets/{ticket.pk}/').status_code,404)
        response=self.client.post('/api/v1/tickets/',{'customer':customer.pk,'subject':'Bad','status':'OPEN','priority':'MEDIUM'},content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post('/workspace/switch/',{'workspace_id':other.pk}).status_code,403)
        self.member.is_active=False
        self.member.save()
        self.assertEqual(self.client.get('/api/v1/tickets/').status_code,403)

    def test_stale_update_conflicts_and_does_not_mutate(self):
        data=self.create_ticket()
        url=f'/api/v1/tickets/{data["id"]}/'
        response=self.client.patch(url,{'subject':'Updated','revision':1},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['revision'],2)
        response=self.client.patch(url,{'subject':'Stale','revision':1},content_type='application/json')
        self.assertEqual(response.status_code,409)
        self.assertEqual(Ticket.objects.get(pk=data['id']).subject,'Updated')

    def test_internal_notes_do_not_count_as_public_response(self):
        data=self.create_ticket()
        url=f'/api/v1/tickets/{data["id"]}/messages/'
        response=self.client.post(url,{'body':'A private note','internal':True,'revision':1},content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        ticket=Ticket.objects.get(pk=data['id'])
        self.assertIsNone(ticket.first_response_at)
        response=self.client.post(url,{'body':'Hello customer','internal':False,'revision':2},content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_response_at)

    def test_resolve_reopen_and_signed_feedback_once(self):
        from desk.services import feedback_token_for,token_digest
        from desk.models import SatisfactionResponse
        data=self.create_ticket()
        url=f'/api/v1/tickets/{data["id"]}/'
        response=self.client.patch(url,{'status':'RESOLVED','revision':1},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        ticket=Ticket.objects.get(pk=data['id'])
        self.assertIsNotNone(ticket.resolved_at)
        raw=feedback_token_for(ticket.pk)
        self.assertEqual(SatisfactionResponse.objects.get(ticket=ticket).token,token_digest(raw))
        self.client.logout()
        feedback=f'/feedback/{raw}/'
        self.assertEqual(self.client.get(feedback).status_code,200)
        self.assertEqual(self.client.post(feedback,{'score':'5','comment':'Great'}).status_code,200)
        self.assertEqual(self.client.post(feedback,{'score':'1'}).status_code,404)
        self.client.force_login(self.user)
        response=self.client.patch(url,{'status':'OPEN','revision':2},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.resolved_at)

    def test_agents_cannot_delete_or_manage_access(self):
        data=self.create_ticket()
        self.member.role='agent'
        self.member.save()
        self.assertEqual(self.client.delete(f'/api/v1/tickets/{data["id"]}/',{'revision':1},content_type='application/json').status_code,403)
        self.assertEqual(self.client.post('/team/',{'email':'someone@example.com','role':'agent'}).status_code,403)
        self.assertEqual(self.client.post('/settings/',{'name':'Bad','timezone':'UTC'}).status_code,403)

    def test_final_admin_is_protected(self):
        response=self.client.post(f'/team/{self.member.pk}/',{'role':'agent','is_active':'on'})
        self.assertEqual(response.status_code,400)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role,'admin')

    def test_notifications_are_recipient_scoped(self):
        from desk.models import ActivityEvent
        other_user=User.objects.create_user('agent@example.com')
        other=Membership.objects.create(workspace=self.workspace,user=other_user)
        event=ActivityEvent.objects.create(workspace=self.workspace,kind='test',description='Test')
        note=Notification.objects.create(recipient=other,event=event)
        self.assertEqual(self.client.post(f'/api/v1/notifications/{note.pk}/read/').status_code,404)
        self.assertEqual(self.client.get('/api/v1/notifications/').json()['count'],0)

    def test_metrics_and_filters_reflect_database_changes(self):
        data=self.create_ticket()
        self.assertEqual(self.client.get('/api/v1/metrics/').json()['metrics']['total'],1)
        self.assertEqual(self.client.get('/api/v1/tickets/?q=Please&status=OPEN').json()['count'],1)
        self.assertEqual(self.client.get('/api/v1/tickets/?q=missing').json()['count'],0)
        self.assertEqual(self.client.delete(f'/api/v1/tickets/{data["id"]}/',{'revision':1},content_type='application/json').status_code,204)
        self.assertEqual(self.client.get('/api/v1/metrics/').json()['metrics']['total'],0)
        self.assertIsNotNone(Ticket.objects.get(pk=data['id']).deleted_at)

    def test_malformed_json_wrong_content_and_invalid_fields_return_errors(self):
        self.assertEqual(self.client.post('/api/v1/tickets/','{oops',content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/v1/tickets/',{'subject':'Wrong type'}).status_code,400)
        self.assertEqual(self.client.post('/api/v1/tickets/','[]',content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/v1/tickets/',{'customer':self.customer.pk,'subject':'Long','status':'OPEN','priority':'MEDIUM','description':'a'*20001},content_type='application/json').status_code,400)

    def test_registration_normalizes_email_and_creates_workspace(self):
        self.client.logout()
        response=self.client.post('/register/',{'name':'New Owner','email':'NEW@EXAMPLE.COM','password1':'UniquePass!2026','password2':'UniquePass!2026','workspace_name':'New Studio'})
        self.assertEqual(response.status_code,302,response.content)
        user=User.objects.get(username='new@example.com')
        self.assertEqual(user.memberships.get().role,'admin')
        self.assertEqual(self.client.get('/').status_code,200)

    def test_invitation_is_hashed_once_and_preserves_existing_admin(self):
        from desk.services import create_invitation,accept_invitation,token_digest
        from django.contrib.auth.models import AnonymousUser
        from desk.models import Invitation
        raw=create_invitation(self.member,{'email':'newagent@example.com','role':'agent'})
        invite=Invitation.objects.get(email='newagent@example.com')
        self.assertEqual(invite.token,token_digest(raw))
        self.client.logout()
        response=self.client.post(f'/invitations/{raw}/',{'name':'New Agent','password1':'UniquePass!2026','password2':'UniquePass!2026'})
        self.assertEqual(response.status_code,302,response.content)
        self.assertEqual(self.client.get(f'/invitations/{raw}/').status_code,404)
        # An old invitation must not downgrade an already active administrator.
        from django.utils import timezone
        import secrets
        raw=secrets.token_urlsafe(32)
        Invitation.objects.create(workspace=self.workspace,email=self.user.email,role='agent',token=token_digest(raw),expires_at=timezone.now()+timezone.timedelta(days=1))
        accept_invitation(raw,self.user)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role,'admin')

    def test_major_pages_render_without_errors(self):
        data=self.create_ticket()
        for path in ['/', '/tickets/','/tickets/new/',f'/tickets/{data["id"]}/',f'/tickets/{data["id"]}/edit/',f'/tickets/{data["id"]}/delete/','/customers/','/customers/new/',f'/customers/{self.customer.pk}/',f'/customers/{self.customer.pk}/edit/','/team/','/activity/','/notifications/','/settings/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)
