"""
URLs for edx-solutions mobile API
"""
from django.conf.urls import patterns, url
from edx_solutions_api_integration.mobile_api import views as mobile_views


urlpatterns = patterns(
    '',
    url(r'^users/organizations/', mobile_views.MobileUsersOrganizationsList.as_view(), name='mobile-users-orgs-list'),
)
