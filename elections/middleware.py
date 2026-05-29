from urllib.parse import quote

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import resolve, Resolver404


class AuthenticationRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or self._is_exempt(request):
            return self.get_response(request)

        if request.path_info.startswith("/api/"):
            return JsonResponse(
                {"detail": "Authentication credentials were not provided."},
                status=401,
            )

        login_url = settings.LOGIN_URL
        next_url = quote(request.get_full_path(), safe="/?=&")
        return redirect(f"{login_url}?next={next_url}")

    def _is_exempt(self, request) -> bool:
        path = request.path_info

        static_url = getattr(settings, "STATIC_URL", "")
        if static_url and path.startswith(static_url):
            return True

        media_url = getattr(settings, "MEDIA_URL", "")
        if media_url and path.startswith(media_url):
            return True

        if path.startswith("/admin/login/"):
            return True

        try:
            match = resolve(path)
        except Resolver404:
            return False

        return match.url_name in {"login", "register"}
