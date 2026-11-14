"""Workspace-scoped pages and public account workflows."""
import hashlib
from functools import wraps
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from . import forms, services
from .analytics import dashboard_data
from .context_processors import resolve_membership
from .models import AIExecution, Customer, Invitation, Membership, SatisfactionResponse, Ticket


def scoped(view):
    @login_required
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        resolve_membership(request)
        return view(request,*args,**kwargs)
    return wrapped


def paginate(request, queryset, per_page=20):
    page = Paginator(queryset,per_page).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page',None)
    return {'page_obj':page,'querystring':params.urlencode()}


def selected_days(request):
    return int(request.GET.get('days',30)) if request.GET.get('days','30') in ['7','30','90'] else 30


def ticket_queryset(member,params):
    qs = member.workspace.tickets.filter(deleted_at__isnull=True).select_related('customer','assignee__user','category')
    q = str(params.get('q',''))[:200].strip()
    if q:
        match = Q(subject__icontains=q)|Q(customer__name__icontains=q)|Q(customer__email__icontains=q)
        if q.replace('SF-','').replace('#','').isdigit():
            match |= Q(number=int(q.replace('SF-','').replace('#','')))
        qs = qs.filter(match)
    if params.get('status') in Ticket.Status.values:
        qs = qs.filter(status=params['status'])
    if params.get('priority') in Ticket.Priority.values:
        qs = qs.filter(priority=params['priority'])
    if params.get('assignee') == 'unassigned':
        qs = qs.filter(assignee__isnull=True)
    elif str(params.get('assignee','')).isdigit():
        qs = qs.filter(assignee_id=params['assignee'])
    sort = params.get('sort','newest')
    if sort == 'priority':
        qs = qs.annotate(priority_order=Case(When(priority='CRITICAL',then=Value(0)),When(priority='HIGH',then=Value(1)),When(priority='MEDIUM',then=Value(2)),default=Value(3),output_field=IntegerField())).order_by('priority_order','-created_at')
    else:
        qs = qs.order_by({'oldest':'created_at','updated':'-updated_at'}.get(sort,'-created_at'),'-pk')
    return qs


def ticket_for(request,pk):
    return get_object_or_404(request.membership.workspace.tickets.filter(deleted_at__isnull=True).select_related('customer','category','assignee__user'),pk=pk)


def form_error(form,error):
    if isinstance(error, ValidationError):
        form.add_error(None,'Please check the record values and try again.')
    else:
        form.add_error(None,str(error))


def generic_form(request,form,title,cancel_url,subtitle='',submit_label='Save changes',status=200):
    return render(request,'desk/form.html',{'form':form,'title':title,'subtitle':subtitle,'cancel_url':cancel_url,'submit_label':submit_label},status=status)


@scoped
@require_GET
def dashboard(request):
    return render(request,'desk/dashboard.html',{'active_nav':'dashboard',**dashboard_data(request.membership.workspace,selected_days(request))})


@scoped
@require_GET
def ticket_list(request):
    member=request.membership
    qs=ticket_queryset(member,request.GET)
    from django.db.models import Count
    counts=dict(member.workspace.tickets.filter(deleted_at__isnull=True).values_list('status').annotate(total=Count('id')))
    return render(request,'desk/ticket_list.html',{'active_nav':'tickets',**paginate(request,qs),'filters':{key:request.GET.get(key,'newest' if key=='sort' else '') for key in ['q','status','priority','assignee','sort']},'memberships':member.workspace.memberships.filter(is_active=True).select_related('user'),'categories':member.workspace.categories.all(),'status_choices':Ticket.Status.choices,'priority_choices':Ticket.Priority.choices,'total_count':qs.count(),'counts':counts})


@scoped
def ticket_create(request):
    form=forms.ScopedTicketForm(request.POST or None,workspace=request.membership.workspace)
    if request.method=='POST' and form.is_valid():
        try:
            ticket=services.create_ticket(request.membership,form.cleaned_data)
            messages.success(request,'Ticket created.')
            return redirect('ticket_detail',pk=ticket.pk)
        except (services.DomainError,ValidationError) as error:
            form_error(form,error)
    return generic_form(request,form,'Create ticket',reverse('ticket_list'),submit_label='Create ticket')


def detail_context(request,ticket,message_form=None,update_form=None):
    survey=SatisfactionResponse.objects.filter(ticket=ticket,submitted_at__isnull=True).first()
    feedback_url=request.build_absolute_uri(reverse('feedback',args=[services.feedback_token_for(ticket.pk)])) if survey and ticket.status=='RESOLVED' else ''
    return {'active_nav':'tickets','ticket':ticket,'customer':ticket.customer,'conversation':ticket.messages.select_related('author','customer'),'events':ticket.activities.select_related('actor','ticket','customer')[:15],'message_form':message_form or forms.MessageForm(initial={'revision':ticket.revision}),'update_form':update_form or forms.TicketUpdateForm(instance=ticket,workspace=request.membership.workspace),'ai_demo':django_settings.AI_PROVIDER=='demo','public_feedback_url':feedback_url}


@scoped
@require_GET
def ticket_detail(request,pk):
    return render(request,'desk/ticket_detail.html',detail_context(request,ticket_for(request,pk)))


@scoped
def ticket_edit(request,pk):
    ticket=ticket_for(request,pk)
    form=forms.ScopedTicketForm(request.POST or None,instance=ticket,workspace=request.membership.workspace)
    status=200
    if request.method=='POST' and form.is_valid():
        try:
            services.update_ticket(request.membership,pk,form.cleaned_data)
            messages.success(request,'Ticket updated.')
            return redirect('ticket_detail',pk=pk)
        except (services.DomainError,ValidationError) as error:
            form_error(form,error)
            status=getattr(error,'status',400)
    return generic_form(request,form,'Edit ticket',reverse('ticket_detail',args=[pk]),status=status)


@scoped
@require_POST
def ticket_update(request,pk):
    ticket=ticket_for(request,pk)
    form=forms.TicketUpdateForm(request.POST,instance=ticket,workspace=request.membership.workspace)
    status=400
    if form.is_valid():
        try:
            services.update_ticket(request.membership,pk,form.cleaned_data)
            messages.success(request,'Ticket details saved.')
            return redirect('ticket_detail',pk=pk)
        except (services.DomainError,ValidationError) as error:
            form_error(form,error)
            status=getattr(error,'status',400)
    return render(request,'desk/ticket_detail.html',detail_context(request,ticket_for(request,pk),update_form=form),status=status)


@scoped
@require_POST
def ticket_reply(request,pk):
    ticket=ticket_for(request,pk)
    form=forms.MessageForm(request.POST)
    status=400
    if form.is_valid():
        try:
            services.add_message(request.membership,pk,form.cleaned_data)
            messages.success(request,'Internal note added.' if form.cleaned_data['internal'] else 'Reply added.')
            return redirect('ticket_detail',pk=pk)
        except (services.DomainError,ValidationError) as error:
            form_error(form,error)
            status=getattr(error,'status',400)
    return render(request,'desk/ticket_detail.html',detail_context(request,ticket_for(request,pk),message_form=form),status=status)


@scoped
def ticket_delete(request,pk):
    if request.membership.role!='admin':
        raise PermissionDenied
    ticket=ticket_for(request,pk)
    if request.method=='POST':
        try:
            services.delete_ticket(request.membership,pk,request.POST.get('revision'))
            messages.success(request,'Ticket deleted.')
            return redirect('ticket_list')
        except services.DomainError as error:
            return render(request,'desk/confirm_delete.html',{'ticket':ticket,'error':str(error)},status=error.status)
    return render(request,'desk/confirm_delete.html',{'ticket':ticket})


def run_ai(request,ticket,data):
    from .ai import analyze_ticket,AIError
    operation=data.get('operation')
    if operation not in ['reply','summary','classify']:
        raise services.DomainError('Choose reply, summary, or classify.')
    services.check_revision(ticket,data.get('revision'))
    if AIExecution.objects.filter(requested_by=request.user,created_at__gte=timezone.now()-timezone.timedelta(minutes=1)).count()>=10:
        raise services.DomainError('Please wait a minute before generating more suggestions.','rate_limited',429)
    try:
        return analyze_ticket(ticket,operation,request.user)
    except AIError as error:
        raise services.DomainError(str(error),'provider_unavailable',503)


@scoped
@require_POST
def ticket_ai(request,pk):
    from .api import parse_json,error_response
    try:
        data=parse_json(request) if request.content_type=='application/json' else request.POST
        return JsonResponse(run_ai(request,ticket_for(request,pk),data))
    except services.DomainError as error:
        return error_response(error)


@scoped
@require_GET
def customer_list(request):
    q=request.GET.get('q','')[:200]
    qs=request.membership.workspace.customers.filter(Q(name__icontains=q)|Q(email__icontains=q)|Q(company__icontains=q))
    return render(request,'desk/customer_list.html',{'active_nav':'customers','q':q,**paginate(request,qs)})


@scoped
def customer_create(request):
    form=forms.CustomerForm(request.POST or None,workspace=request.membership.workspace)
    if request.method=='POST' and form.is_valid():
        customer=services.save_customer(request.membership,form)
        messages.success(request,'Customer created.')
        return redirect('customer_detail',pk=customer.pk)
    return generic_form(request,form,'Add customer',reverse('customer_list'),submit_label='Add customer')


@scoped
@require_GET
def customer_detail(request,pk):
    customer=get_object_or_404(request.membership.workspace.customers,pk=pk)
    return render(request,'desk/customer_detail.html',{'active_nav':'customers','customer':customer,'tickets':customer.tickets.filter(deleted_at__isnull=True).select_related('customer','assignee__user'),'events':customer.activities.select_related('actor','ticket','customer')[:30]})


@scoped
def customer_edit(request,pk):
    customer=get_object_or_404(request.membership.workspace.customers,pk=pk)
    form=forms.CustomerForm(request.POST or None,instance=customer,workspace=request.membership.workspace)
    if request.method=='POST' and form.is_valid():
        services.save_customer(request.membership,form)
        messages.success(request,'Customer updated.')
        return redirect('customer_detail',pk=pk)
    return generic_form(request,form,'Edit customer',reverse('customer_detail',args=[pk]))


@scoped
def team(request):
    member=request.membership
    form=forms.InvitationForm(request.POST or None)
    invitation_url=''
    status=200
    if request.method=='POST':
        if member.role!='admin':
            raise PermissionDenied
        if form.is_valid():
            try:
                raw=services.create_invitation(member,form.cleaned_data)
                invitation_url=request.build_absolute_uri(reverse('invitation_accept',args=[raw]))
                form=forms.InvitationForm()
                messages.success(request,'Invitation link created. Share it with your teammate.')
            except services.DomainError as error:
                form_error(form,error)
                status=error.status
    return render(request,'desk/team.html',{'active_nav':'team','memberships':member.workspace.memberships.select_related('user').order_by('-is_active','user__first_name'),'invitation_form':form,'invitations':member.workspace.invitations.filter(accepted_at__isnull=True,expires_at__gt=timezone.now()),'invitation_url':invitation_url},status=status)


@scoped
@require_POST
def member_update(request,pk):
    if request.membership.role!='admin':
        raise PermissionDenied
    get_object_or_404(request.membership.workspace.memberships,pk=pk)
    form=forms.MemberForm(request.POST)
    if form.is_valid():
        try:
            services.update_member(request.membership,pk,form.cleaned_data)
            messages.success(request,'Team access updated.')
        except services.DomainError as error:
            return HttpResponse(str(error),status=error.status)
    else:
        return HttpResponse('Invalid team role.',status=400)
    return redirect('team')


@scoped
@require_GET
def activity(request):
    return render(request,'desk/activity.html',{'active_nav':'activity',**paginate(request,request.membership.workspace.activities.select_related('actor','ticket','customer'))})


@scoped
@require_GET
def notifications(request):
    return render(request,'desk/notifications.html',{'active_nav':'notifications',**paginate(request,request.membership.notifications.select_related('event__actor','event__ticket','event__customer'))})


@scoped
@require_POST
def notification_read(request,pk):
    item=get_object_or_404(request.membership.notifications,pk=pk)
    if not item.read_at:
        item.read_at=timezone.now()
        item.save(update_fields=['read_at'])
    return redirect('notifications')


@scoped
@require_POST
def notifications_read_all(request):
    request.membership.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect('notifications')


@scoped
def settings(request):
    form=forms.WorkspaceForm(request.POST or None,instance=request.membership.workspace)
    if request.method=='POST':
        if request.membership.role!='admin':
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                form.save()
                services.record_event(request.membership.workspace,request.user,'workspace.updated','updated workspace settings')
            messages.success(request,'Workspace settings saved.')
            return redirect('settings')
    return render(request,'desk/settings.html',{'active_nav':'settings','form':form})


@scoped
@require_POST
def workspace_switch(request):
    target=request.POST.get('workspace_id','')
    if not target.isdigit() or not Membership.objects.filter(user=request.user,workspace_id=target,is_active=True).exists():
        raise PermissionDenied
    request.session['workspace_id']=int(target)
    return redirect('dashboard')


class ThrottledLoginView(LoginView):
    template_name='registration/login.html'
    authentication_form=forms.LoginForm
    redirect_authenticated_user=True

    def dispatch(self,request,*args,**kwargs):
        key=f'{request.META.get("REMOTE_ADDR","")}:{request.POST.get("username","").lower()[:254]}'
        self.throttle_key='login:'+hashlib.sha256(key.encode()).hexdigest()
        if request.method=='POST' and cache.get(self.throttle_key,0)>=8:
            form=self.get_form()
            form.add_error(None,'Too many sign-in attempts. Try again in 15 minutes.')
            return self.render_to_response(self.get_context_data(form=form),status=429)
        return super().dispatch(request,*args,**kwargs)

    def form_invalid(self,form):
        cache.set(self.throttle_key,cache.get(self.throttle_key,0)+1,900)
        return super().form_invalid(form)

    def form_valid(self,form):
        cache.delete(self.throttle_key)
        return super().form_valid(form)


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form=forms.RegisterForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            user,workspace=services.register_workspace(form.cleaned_data)
            auth_login(request,user)
            request.session['workspace_id']=workspace.pk
            return redirect('dashboard')
        except (services.DomainError,IntegrityError) as error:
            form.add_error(None,'This email is already registered. Sign in instead.')
    return render(request,'registration/register.html',{'form':form})


def invitation_accept(request,token):
    invite=Invitation.objects.filter(token=services.token_digest(token),accepted_at__isnull=True,expires_at__gt=timezone.now()).select_related('workspace').first()
    if not invite:
        return render(request,'desk/invitation_accept.html',{'valid':False},status=404)
    form=None
    error=''
    status=200
    existing=User.objects.filter(Q(email__iexact=invite.email)|Q(username__iexact=invite.email)).exists()
    if not request.user.is_authenticated and not existing:
        data=request.POST.copy() if request.method=='POST' else None
        if data is not None:
            data['email']=invite.email
        form=forms.AccountForm(data,initial={'email':invite.email})
        form.fields['email'].disabled=True
    elif not request.user.is_authenticated:
        error='Sign in with your existing account to accept this invitation.'
    if request.method=='POST' and (form is None or form.is_valid()):
        try:
            user,workspace=services.accept_invitation(token,request.user,form.cleaned_data if form else None)
            auth_login(request,user)
            request.session['workspace_id']=workspace.pk
            messages.success(request,'You have joined the workspace.')
            return redirect('dashboard')
        except services.DomainError as failure:
            error=str(failure)
            status=failure.status
    return render(request,'desk/invitation_accept.html',{'valid':True,'invite':invite,'form':form,'error':error},status=status)


from django.contrib.auth.models import User

def feedback(request,token):
    try:
        pk=signing.Signer(salt='feedback').unsign(token)
        if not pk.isdigit():
            raise signing.BadSignature
    except signing.BadSignature:
        return render(request,'desk/feedback.html',{'invalid':True},status=404)
    survey=SatisfactionResponse.objects.filter(ticket_id=pk,token=services.token_digest(token),ticket__deleted_at__isnull=True,ticket__status='RESOLVED').select_related('ticket__workspace').first()
    if not survey or survey.submitted_at:
        return render(request,'desk/feedback.html',{'invalid':True},status=404)
    form=forms.FeedbackForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            survey=SatisfactionResponse.objects.select_for_update().get(pk=survey.pk)
            if survey.submitted_at:
                return render(request,'desk/feedback.html',{'invalid':True},status=409)
            survey.score=form.cleaned_data['score']
            survey.comment=form.cleaned_data['comment']
            survey.submitted_at=timezone.now()
            survey.full_clean()
            survey.save()
        return render(request,'desk/feedback.html',{'saved':True})
    return render(request,'desk/feedback.html',{'ticket':survey.ticket,'form':form})


@require_GET
def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status':'ok'})


def csrf_failure(request,reason=''):
    if request.path.startswith('/api/'):
        return JsonResponse({'error':{'code':'csrf_failed','message':'Refresh the page and try again.'}},status=403)
    return HttpResponse('Your form session expired. Refresh the page and try again.',status=403)
