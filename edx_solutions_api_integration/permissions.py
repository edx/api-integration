""" Permissions classes utilized by Django REST Framework """
import logging

from django.conf import settings
from student.models import CourseEnrollment
from edx_solutions_api_integration.courseware_access import get_course_key
from edx_solutions_api_integration.utils import get_client_ip_address, address_exists_in_network
from rest_framework import permissions, generics, filters, pagination, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from edx_rest_framework_extensions.authentication import JwtAuthentication
from openedx.core.lib.api.authentication import (
    SessionAuthenticationAllowInactiveUser,
    OAuth2AuthenticationAllowInactiveUser,
)
from edx_solutions_api_integration.utils import str2bool
from edx_solutions_api_integration.models import APIUser as User

log = logging.getLogger(__name__)


class ApiKeyHeaderPermission(permissions.BasePermission):
    """
    Check for permissions by matching the configured API key and header

    """
    def has_permission(self, request, view):
        """
        If settings.DEBUG is True and settings.EDX_API_KEY is not set or None,
        then allow the request. Otherwise, allow the request if and only if
        settings.EDX_API_KEY is set and the X-Edx-Api-Key HTTP header is
        present in the request and matches the setting.
        """

        debug_enabled = settings.DEBUG
        api_key = getattr(settings, "EDX_API_KEY", None)

        # DEBUG mode rules over all else
        # Including the api_key check here ensures we don't break the feature locally
        if debug_enabled and api_key is None:
            log.warn("EDX_API_KEY Override: Debug Mode")
            return True

        # If we're not DEBUG, we need a local api key
        if api_key is None:
            return False

        # The client needs to present the same api key
        header_key = request.META.get('HTTP_X_EDX_API_KEY')
        if header_key is None:
            try:
                header_key = request.META['headers'].get('X-Edx-Api-Key')
            except KeyError:
                return False
            if header_key is None:
                return False

        # The api key values need to be the same
        if header_key != api_key:
            return False

        # Allow the request to take place
        return True


class IPAddressRestrictedPermission(permissions.BasePermission):
    """
    Check for permissions by matching the request IP address
    against the allowed ip address(s)
    """

    def has_permission(self, request, view):
        ip_address = get_client_ip_address(request)
        allowed_ip_addresses = getattr(settings, 'API_ALLOWED_IP_ADDRESSES', None)
        if allowed_ip_addresses:
            for allowed_ip_address in allowed_ip_addresses:
                if '/' in allowed_ip_address:
                    is_allowed = address_exists_in_network(ip_address, allowed_ip_address)
                    if is_allowed:
                        return is_allowed
                else:
                    if ip_address == allowed_ip_address:
                        return True
            log.warn("{} is not allowed to access Api".format(ip_address))  # pylint: disable=W1202
            return False
        else:
            return True


class IsStaffOrEnrolled(permissions.BasePermission):
    """
    Permission that allows access to staff users or the enrolled users of a course.
    """
    def has_permission(self, request, view):
        user = request.user
        course_id = request.GET.get('course_id') \
                    or request.parser_context.get('kwargs', {}).get('course_id', None)
        course_key = get_course_key(course_id)
        if course_key:
            return user.is_staff or CourseEnrollment.is_enrolled(request.user, course_key)
        return False


class IsStaffView(permissions.BasePermission):
    """
    Permission that checks to see if the user is staff.
    """

    def has_permission(self, request, view):
        return request.user.is_staff


class IsStaffOrReadOnlyView(permissions.BasePermission):
    """
    Permission that checks to see if the user is staff and the view is POST.
    """

    def has_permission(self, request, view):
        if not request.method == 'GET':
            return request.user.is_staff
        return True


class IdsInFilterBackend(filters.BaseFilterBackend):
    """
        This backend support filtering queryset by a list of ids
    """
    def filter_queryset(self, request, queryset, view):
        """
        Parse querystring to get ids and the filter the queryset
        Max of 800 values are allowed for performance reasons
        (800 satisfies a specific client integration use case)
        """
        upper_bound = getattr(settings, 'API_LOOKUP_UPPER_BOUND', 800)
        ids = request.query_params.get('ids')
        if ids:
            ids = ids.split(",")[:upper_bound]
            return queryset.filter(id__in=ids)
        return queryset


class HasOrgsFilterBackend(filters.BaseFilterBackend):
    """
        This backend support filtering users with and organization association or not
    """
    def filter_queryset(self, request, queryset, view):
        """
        Parse querystring base on has_organizations query param
        """
        has_orgs = request.query_params.get('has_organizations', None)
        if has_orgs:
            if str2bool(has_orgs):
                queryset = queryset.filter(organizations__id__gt=0)
            else:
                queryset = queryset.exclude(id__in=User.objects.filter(organizations__id__gt=0).
                                            values_list('id', flat=True))
        return queryset.distinct()


class PermissionMixin(object):
    """
    Mixin to set custom permission_classes
    """
    permission_classes = (ApiKeyHeaderPermission, IPAddressRestrictedPermission)


class MobilePermissionMixin(object):
    """
    Mixin to set custom permission_classes
    """
    authentication_classes = (
        JwtAuthentication,
        OAuth2AuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, )


class TokenBasedAuthenticationMixin(object):
    """
    Mixin to set custom authentication_classes
    """
    authentication_classes = (OAuth2AuthenticationAllowInactiveUser, )


class FilterBackendMixin(object):
    """
    Mixin to set custom filter_backends
    """
    filter_backends = (filters.DjangoFilterBackend, IdsInFilterBackend,)


class CustomPagination(pagination.PageNumberPagination):
    """
    Class having custom pagination overrides
    """

    def get_paginated_response(self, data):
        """
        creates custom pagination response
        """
        return Response({
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'num_pages': self.page.paginator.num_pages,
            'count': self.page.paginator.count,
            'results': data
        })

    def get_page_size(self, request):
        """
        override to return None if page_size parameter in request is zero.
        We should not paginate results in that case.
        """
        default_page_size = getattr(settings, 'API_PAGE_SIZE', 20)
        page_size = int(request.query_params.get('page_size', default_page_size))
        if page_size == 0:
            return None
        elif page_size > 100:
            return default_page_size
        else:
            return page_size


class PaginationMixin(object):
    """
    Mixin to set custom pagination support
    """
    pagination_class = CustomPagination


class SecureAPIView(PermissionMixin, APIView):
    """
    View used for protecting access to specific workflows
    """
    pass


class SecureListAPIView(PermissionMixin,
                        FilterBackendMixin,
                        PaginationMixin,
                        generics.ListAPIView):
    """
    Inherited from ListAPIView
    """
    pass


class SecureListCreateAPIView(PermissionMixin,
                        FilterBackendMixin,
                        PaginationMixin,
                        generics.ListCreateAPIView):
    """
    Inherited from ListCreateAPIView
    """
    pass


class SecureRetrieveUpdateDestroyAPIView(PermissionMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    Inherited from RetrieveUpdateDestroyAPIView
    """
    pass


class SecureRetrieveUpdateAPIView(PermissionMixin, generics.RetrieveUpdateAPIView):
    """
    Inherited from RetrieveUpdateAPIView
    """
    pass


class SecureCreateAPIView(PermissionMixin, generics.CreateAPIView):
    """
    Inherited from CreateAPIView
    """
    pass


class MobileListAPIView(MobilePermissionMixin, FilterBackendMixin, PaginationMixin, generics.ListAPIView):
    """
    Base view for mobile list view APIs
    """


class MobileAPIView(MobilePermissionMixin, APIView):
    """
    Inherited from APIView
    """
    pass


class TokenBasedAPIView(TokenBasedAuthenticationMixin, APIView):
    """
    Inherited from APIView
    """
    pass


class MobileListCreateAPIView(MobilePermissionMixin,
                        FilterBackendMixin,
                        PaginationMixin,
                        generics.ListCreateAPIView):
    """
    Inherited from ListCreateAPIView
    """
    pass


class MobileRetrieveUpdateAPIView(MobilePermissionMixin, generics.RetrieveUpdateAPIView):
    """
    Inherited from RetrieveUpdateAPIView
    """
    pass


class MobileRetrieveUpdateDestroyAPIView(MobilePermissionMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    Inherited from RetrieveUpdateDestroyAPIView
    """
    pass


class MobileDestroyAPIView(MobilePermissionMixin, generics.DestroyAPIView):
    """
    Inherited from DestroyAPIView
    """
    pass


class SecureModelViewSet(PermissionMixin, viewsets.ModelViewSet):
    """
    ModelViewSet used for protecting access to specific workflows
    """
    pass


class SecurePaginatedModelViewSet(PaginationMixin, SecureModelViewSet):
    """
    ModelViewSet used for pagination and protecting access to specific workflows
    """
    pass
