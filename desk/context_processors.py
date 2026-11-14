from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from .models import Membership


def resolve_membership(request, required=True):
    if hasattr(request, "membership"):
        return request.membership
    membership = None
    if request.user.is_authenticated:
        choices = (
            Membership.objects.filter(user=request.user, is_active=True)
            .select_related("workspace", "user")
            .order_by("pk")
        )
        selected = request.session.get("workspace_id")
        membership = choices.filter(workspace_id=selected).first() if selected else None
        if not membership:
            membership = choices.first()
        if membership:
            request.session["workspace_id"] = membership.workspace_id
            try:
                timezone.activate(ZoneInfo(membership.workspace.timezone))
            except ZoneInfoNotFoundError:
                timezone.activate("UTC")
    if required and membership is None:
        raise PermissionDenied("No active workspace membership.")
    request.membership = membership
    return membership


def workspace_context(request):
    member = resolve_membership(request, required=False)
    return {
        "workspace": member.workspace if member else None,
        "membership": member,
        "is_admin": bool(member and member.role == "admin"),
        "unread_count": member.notifications.filter(read_at__isnull=True).count() if member else 0,
        "workspace_memberships": request.user.memberships.filter(is_active=True).select_related(
            "workspace"
        )
        if request.user.is_authenticated
        else [],
        "demo_mode": bool(
            getattr(settings, "DEMO_MODE", False)
            and (not member or member.workspace.slug == "supportflow-demo")
        ),
        "ai_demo": settings.AI_PROVIDER == "demo",
    }
