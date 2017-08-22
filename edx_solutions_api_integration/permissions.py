""" Permissions classes utilized by Django REST Framework """
import logging

from django.conf import settings

from rest_framework import permissions, generics, filters, pagination, serializers, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response

from openedx.core.lib.api.authentication import OAuth2AuthenticationAllowInactiveUser
from edx_solutions_api_integration.utils import (
    get_client_ip_address, address_exists_in_network, str2bool,
    has_api_key_permission,
)
from edx_solutions_api_integration.models import APIUser as User

log = logging.getLogger(__name__)


class ApiKeyHeaderPermission(permissions.BasePermission):
    """
    Check for permissions by matching the configured API key and header

    """
    def has_permission(self, request, view):
        """
        checks if user has api key header permission
        """
        return has_api_key_permission(request)

class ApiKeyOrOAuth2Permission(permissions.BasePermission):
    """
    Check for permissions by matching the configured API key or oAuth2

    """
    def has_permission(self, request, view):
        """
        checks if user has api key or oauth2 permission
        """
        return (request.user and request.user.is_authenticated()) or has_api_key_permission(request)

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


class ApiKeyOrOAuth2AuthenticationMixin(object):
    """
    Mixin to set OAuth2 or API key header authentication
    """
    authentication_classes = (
        OAuth2AuthenticationAllowInactiveUser,
    )
    permission_classes = (ApiKeyOrOAuth2Permission, IPAddressRestrictedPermission)


class SecureAPIView(APIView):
    """
    View used for protecting access to specific workflows
    """
    permission_classes = (ApiKeyHeaderPermission, )


class PermissionMixin(object):
    """
    Mixin to set custom permission_classes
    """
    permission_classes = (ApiKeyHeaderPermission, IPAddressRestrictedPermission)


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


class SecureListAPIView(PermissionMixin,
                        FilterBackendMixin,
                        PaginationMixin,
                        generics.ListAPIView):
    """
    Inherited from ListAPIView
    """
    pass


class ApiKeyOrOAuth2SecuredListAPIView(
        ApiKeyOrOAuth2AuthenticationMixin,
        FilterBackendMixin,
        PaginationMixin,
        generics.ListAPIView
    ):
    """
    List API view with OAuth2 or API key header Authentication
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
