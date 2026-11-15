from django.utils import timezone


def reset_timezone(get_response):
    """Keep a workspace timezone from leaking into the next request on this thread."""

    def middleware(request):
        try:
            return get_response(request)
        finally:
            timezone.deactivate()

    return middleware
