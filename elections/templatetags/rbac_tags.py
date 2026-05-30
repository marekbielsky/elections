from django import template

from elections.rbac import has_permission

register = template.Library()


@register.filter(name="has_app_permission")
def has_app_permission(request, permission_code: str) -> bool:
    if request is None or not permission_code:
        return False
    return has_permission(request, permission_code)
