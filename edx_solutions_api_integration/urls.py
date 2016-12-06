# pylint: disable=C0103
"""
    The URI scheme for resources is as follows:
        Resource type: /api/{resource_type}
        Specific resource: /api/{resource_type}/{resource_id}

    The remaining URIs provide information about the API and/or module
        System: General context and intended usage
        API: Top-level description of overall API (must live somewhere)
"""

from django.conf.urls import include, patterns, url
from django.db import transaction

from rest_framework.routers import DefaultRouter

from edx_solutions_organizations.views import OrganizationsViewSet
from edx_solutions_api_integration.system import views as system_views
from edx_solutions_projects import views as project_views

urlpatterns = patterns(
    '',
    url(r'^$', system_views.ApiDetail.as_view()),
    url(r'^system$', system_views.SystemDetail.as_view()),
    url(r'^users/*', include('edx_solutions_api_integration.users.urls')),
    url(r'^groups/*', include('edx_solutions_api_integration.groups.urls')),
    url(r'^sessions/*', include('edx_solutions_api_integration.sessions.urls')),
    url(r'^courses/', include('edx_solutions_api_integration.courses.urls')),
    url(r'^organizations/*', include('edx_solutions_organizations.urls')),
    # we have to explicitly define url for workgroup users detail view
    # to wrap it around non_atomic_requests decorator
    url(
        r'^workgroups/(?P<pk>\d+)/users/?$',
        transaction.non_atomic_requests(project_views.WorkgroupsViewSet.as_view({
            'get': 'users',
            'post': 'users',
            'delete': 'users',
        })),
        name='workgroup-users-detail'
    ),
)

server_api_router = DefaultRouter()
server_api_router.register(r'organizations', OrganizationsViewSet)

# Project-related ViewSets
server_api_router.register(r'projects', project_views.ProjectsViewSet)
server_api_router.register(r'workgroups', project_views.WorkgroupsViewSet)
server_api_router.register(r'submissions', project_views.WorkgroupSubmissionsViewSet)
server_api_router.register(r'workgroup_reviews', project_views.WorkgroupReviewsViewSet)
server_api_router.register(r'submission_reviews', project_views.WorkgroupSubmissionReviewsViewSet)
server_api_router.register(r'peer_reviews', project_views.WorkgroupPeerReviewsViewSet)
server_api_router.register(r'groups', project_views.GroupViewSet)
server_api_router.register(r'users', project_views.UserViewSet)
urlpatterns += server_api_router.urls
