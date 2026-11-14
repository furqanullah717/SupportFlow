from django.contrib import admin
from .models import (
    AIExecution, ActivityEvent, Category, Customer, Invitation, Membership,
    Message, Notification, SatisfactionResponse, Ticket, Workspace,
)

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('number', 'subject', 'workspace', 'status', 'priority', 'assignee')
    list_filter = ('workspace', 'status', 'priority')
    search_fields = ('subject', 'customer__name', 'customer__email')

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'company', 'workspace')
    search_fields = ('name', 'email', 'company')

for model in [Workspace, Membership, Category, Message, ActivityEvent, Notification, SatisfactionResponse, Invitation, AIExecution]:
    admin.site.register(model)
