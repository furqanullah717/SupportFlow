"""Session-authenticated JSON API. Unsafe requests require Django CSRF tokens."""
import json
from functools import wraps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from . import forms, services
from .analytics import dashboard_data
from .context_processors import resolve_membership
from .models import Customer, Membership, Ticket
from .views import run_ai, selected_days, ticket_queryset


def error_response(error):
    value={'code':error.code,'message':error.message}
    if error.fields:
        value['fields']=error.fields
    return JsonResponse({'error':value},status=error.status)


def endpoint(methods,public=False):
    def decorator(view):
        @wraps(view)
        def wrapped(request,*args,**kwargs):
            try:
                if request.method not in methods:
                    response=error_response(services.DomainError('Method not allowed.','method_not_allowed',405))
                    response['Allow']=', '.join(methods)
                    return response
                if not public:
                    if not request.user.is_authenticated:
                        raise services.DomainError('Sign in to continue.','unauthenticated',401)
                    resolve_membership(request)
                return view(request,*args,**kwargs)
            except services.DomainError as error:
                return error_response(error)
            except (Http404,Ticket.DoesNotExist,Customer.DoesNotExist,Membership.DoesNotExist):
                return error_response(services.DomainError('Record not found.','not_found',404))
            except PermissionDenied:
                return error_response(services.DomainError('You do not have access to this action.','forbidden',403))
            except ValidationError as error:
                return error_response(services.DomainError('Check the submitted values.','validation_error',400,getattr(error,'message_dict',None)))
            except IntegrityError:
                return error_response(services.DomainError('The record conflicts with an existing record.','conflict',409))
        return wrapped
    return decorator


def parse_json(request):
    if request.content_type!='application/json':
        raise services.DomainError('Send an application/json request.','invalid_content_type',400)
    if len(request.body)>65536:
        raise services.DomainError('The request is too large.','payload_too_large',413)
    try:
        data=json.loads(request.body.decode('utf-8'))
    except (ValueError,UnicodeDecodeError,RecursionError):
        raise services.DomainError('The request must contain valid JSON.','invalid_json',400)
    if not isinstance(data,dict):
        raise services.DomainError('The JSON body must be an object.','invalid_json',400)
    return data


def valid_form(form):
    if not form.is_valid():
        raise services.DomainError('Check the submitted values.','validation_error',400,{key:[str(value) for value in values] for key,values in form.errors.items()})
    return form.cleaned_data


def ticket_json(ticket):
    return {'id':ticket.pk,'number':ticket.number,'subject':ticket.subject,'description':ticket.description,'status':ticket.status,'priority':ticket.priority,'customer':ticket.customer_id,'customer_name':ticket.customer.name,'assignee':ticket.assignee_id,'category':ticket.category_id,'revision':ticket.revision,'created_at':ticket.created_at.isoformat(),'updated_at':ticket.updated_at.isoformat(),'first_response_at':ticket.first_response_at.isoformat() if ticket.first_response_at else None,'resolved_at':ticket.resolved_at.isoformat() if ticket.resolved_at else None}


def customer_json(customer):
    return {key:getattr(customer,key) for key in ['id','name','email','phone','company','notes']}


def message_json(message):
    return {'id':message.pk,'body':message.body,'internal':message.internal,'kind':message.kind,'author':message.author_id,'customer':message.customer_id,'created_at':message.created_at.isoformat()}


def event_json(event):
    return {'id':event.pk,'kind':event.kind,'description':event.description,'ticket':event.ticket_id,'customer':event.customer_id,'actor':event.actor_id,'changes':event.changes,'created_at':event.created_at.isoformat()}


def paged(request,qs,serialize):
    try:
        number=int(request.GET.get('page',1))
        size=min(max(int(request.GET.get('page_size',20)),1),100)
        if number<1:
            raise ValueError
    except (TypeError,ValueError):
        raise services.DomainError('Use a valid positive page number.','invalid_pagination',400)
    paginator=Paginator(qs,size)
    if number>paginator.num_pages:
        raise Http404
    page=paginator.page(number)
    return JsonResponse({'results':[serialize(item) for item in page],'count':paginator.count,'page':number,'pages':paginator.num_pages,'page_size':size})


def get_ticket(request,pk):
    return get_object_or_404(request.membership.workspace.tickets.filter(deleted_at__isnull=True).select_related('customer'),pk=pk)


@endpoint(['GET','POST'])
def tickets(request):
    if request.method=='GET':
        return paged(request,ticket_queryset(request.membership,request.GET),ticket_json)
    form=forms.ScopedTicketForm(parse_json(request),workspace=request.membership.workspace)
    ticket=services.create_ticket(request.membership,valid_form(form))
    return JsonResponse(ticket_json(ticket),status=201)


@endpoint(['GET','PATCH','DELETE'])
def ticket_detail(request,pk):
    ticket=get_ticket(request,pk)
    if request.method=='GET':
        return JsonResponse(ticket_json(ticket))
    data=parse_json(request)
    if request.method=='DELETE':
        services.delete_ticket(request.membership,pk,data.get('revision'))
        return HttpResponse(status=204)
    # PATCH preserves omitted fields while still validating each supplied relationship.
    original={key:getattr(ticket,key+'_id') if key in ['customer','assignee','category'] else getattr(ticket,key) for key in ['customer','subject','description','status','priority','assignee','category']}
    original.update(data)
    form=forms.ScopedTicketForm(original,instance=ticket,workspace=request.membership.workspace)
    result=services.update_ticket(request.membership,pk,valid_form(form))
    return JsonResponse(ticket_json(result))


@endpoint(['GET','POST'])
def ticket_messages(request,pk):
    ticket=get_ticket(request,pk)
    if request.method=='GET':
        return paged(request,ticket.messages.select_related('author','customer'),message_json)
    form=forms.MessageForm(parse_json(request))
    message=services.add_message(request.membership,pk,valid_form(form))
    result=message_json(message)
    ticket.refresh_from_db()
    result['revision']=ticket.revision
    return JsonResponse(result,status=201)


@endpoint(['POST'])
def ticket_ai(request,pk):
    return JsonResponse(run_ai(request,get_ticket(request,pk),parse_json(request)))


@endpoint(['GET','POST'])
def customers(request):
    if request.method=='GET':
        from django.db.models import Q
        q=request.GET.get('q','')[:200]
        return paged(request,request.membership.workspace.customers.filter(Q(name__icontains=q)|Q(email__icontains=q)|Q(company__icontains=q)),customer_json)
    form=forms.CustomerForm(parse_json(request),workspace=request.membership.workspace)
    valid_form(form)
    return JsonResponse(customer_json(services.save_customer(request.membership,form)),status=201)


@endpoint(['GET','PATCH'])
def customer_detail(request,pk):
    customer=get_object_or_404(request.membership.workspace.customers,pk=pk)
    if request.method=='GET':
        return JsonResponse(customer_json(customer))
    data=customer_json(customer)
    data.update(parse_json(request))
    form=forms.CustomerForm(data,instance=customer,workspace=request.membership.workspace)
    valid_form(form)
    return JsonResponse(customer_json(services.save_customer(request.membership,form)))


@endpoint(['GET'])
def team(request):
    return paged(request,request.membership.workspace.memberships.select_related('user').order_by('pk'),lambda item:{'id':item.pk,'name':str(item),'email':item.user.email,'role':item.role,'is_active':item.is_active})


@endpoint(['GET'])
def notifications(request):
    return paged(request,request.membership.notifications.select_related('event'),lambda item:{'id':item.pk,'read_at':item.read_at.isoformat() if item.read_at else None,'event':event_json(item.event)})


@endpoint(['POST'])
def notification_read(request,pk):
    item=get_object_or_404(request.membership.notifications,pk=pk)
    if not item.read_at:
        item.read_at=timezone.now()
        item.save(update_fields=['read_at'])
    return JsonResponse({'id':item.pk,'read_at':item.read_at.isoformat()})


@endpoint(['POST'])
def notifications_read_all(request):
    count=request.membership.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({'updated':count})


@endpoint(['GET'])
def activity(request):
    return paged(request,request.membership.workspace.activities.select_related('actor','ticket','customer'),event_json)


@endpoint(['GET'])
def metrics(request):
    data=dashboard_data(request.membership.workspace,selected_days(request))
    return JsonResponse({'metrics':data['metrics'],'trends':data['trends'],'status_counts':data['status_counts'],'days':data['days'],'team_performance':[{'member':row['member'].pk,'name':str(row['member']),'assigned':row['assigned'],'resolved':row['resolved'],'response_time':row['response_time']} for row in data['team_performance']]})


@endpoint(['GET'])
def me(request):
    member=request.membership
    return JsonResponse({'id':request.user.pk,'name':request.user.get_full_name(),'email':request.user.email,'workspace':{'id':member.workspace_id,'name':member.workspace.name},'role':member.role})


@endpoint(['GET'],public=True)
def csrf(request):
    return JsonResponse({'csrfToken':get_token(request)})
