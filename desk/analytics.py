from collections import Counter
from datetime import timedelta
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F
from django.db.models.functions import TruncDate
from django.utils import timezone
from .models import SatisfactionResponse, Ticket


def dashboard_data(workspace, days=30):
    today = timezone.localdate()
    start = today - timedelta(days=days-1)
    tickets = workspace.tickets.filter(deleted_at__isnull=True,created_at__date__gte=start)
    counts = dict(tickets.values_list('status').annotate(total=Count('id')))
    duration = ExpressionWrapper(F('first_response_at')-F('created_at'),output_field=DurationField())
    average = tickets.filter(first_response_at__isnull=False).aggregate(value=Avg(duration))['value']
    feedback = SatisfactionResponse.objects.filter(ticket__in=tickets,submitted_at__isnull=False)
    count = feedback.count()
    metrics = {'total':tickets.count(),'open':counts.get('OPEN',0),'in_progress':counts.get('IN_PROGRESS',0),'pending':counts.get('PENDING',0),'resolved':counts.get('RESOLVED',0),'response_time':average.total_seconds()/60 if average else None,'csat':100*feedback.filter(score__gte=4).count()/count if count else None,'csat_count':count}
    tz = timezone.get_current_timezone()
    created = dict(tickets.annotate(day=TruncDate('created_at',tzinfo=tz)).values_list('day').annotate(total=Count('id')))
    resolved = dict(tickets.filter(resolved_at__isnull=False).annotate(day=TruncDate('resolved_at',tzinfo=tz)).values_list('day').annotate(total=Count('id')))
    step = 1 if days == 7 else 3 if days == 30 else 9
    trends = []
    cursor = start
    while cursor <= today:
        end = min(cursor+timedelta(days=step-1),today)
        dates = [cursor+timedelta(days=i) for i in range((end-cursor).days+1)]
        trends.append({'label':cursor.strftime('%b %d'),'created':sum(created.get(d,0) for d in dates),'resolved':sum(resolved.get(d,0) for d in dates)})
        cursor = end+timedelta(days=1)
    maximum = max([max(point['created'],point['resolved']) for point in trends] or [1]) or 1
    for point in trends:
        point.update(created_height=round(point['created']/maximum*100,1),resolved_height=round(point['resolved']/maximum*100,1))
    rows = []
    for member in workspace.memberships.filter(is_active=True).select_related('user'):
        own = tickets.filter(assignee=member)
        response = own.filter(first_response_at__isnull=False).aggregate(value=Avg(duration))['value']
        rows.append({'member':member,'assigned':own.count(),'resolved':own.filter(status='RESOLVED').count(),'response_time':response.total_seconds()/60 if response else None})
    colors = ['#3d89bf','#7367d8','#c2943e','#389777']
    status_counts = [{'key':key,'label':label,'value':counts.get(key,0),'color':colors[i],'percentage':round(counts.get(key,0)/metrics['total']*100,2) if metrics['total'] else 0} for i,(key,label) in enumerate(Ticket.Status.choices)]
    return {'metrics':metrics,'trends':trends,'trend_max':maximum,'team_performance':rows,'recent_activity':workspace.activities.filter(created_at__date__gte=start).select_related('actor','ticket','customer')[:8],'status_counts':status_counts,'days':days,'period_label':f'last {days} days'}
