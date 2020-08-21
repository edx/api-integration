""" Users API URI specification """
from django.conf import settings
from django.conf.urls import url
from django.db import transaction

from rest_framework.urlpatterns import format_suffix_patterns

from edx_solutions_api_integration.users import views as users_views

COURSE_ID_PATTERN = settings.COURSE_ID_PATTERN


urlpatterns = [
    url(r'^metrics/cities/$', users_views.UsersMetricsCitiesList.as_view(), name='apimgr-users-metrics-cities-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/courses/grades$',
        users_views.UsersCoursesGradesList.as_view(), name='users-courses-grades-list'),
    url(
        r'^(?P<user_id>[a-zA-Z0-9]+)/courses/{0}/grades$'.format(COURSE_ID_PATTERN),
        transaction.non_atomic_requests(users_views.UsersCoursesGradesDetail.as_view()),
        name='users-courses-grades-detail'
    ),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/courses/{0}/metrics/social/$'.format(COURSE_ID_PATTERN),
        users_views.UsersSocialMetrics.as_view(), name='users-social-metrics'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/courses/{0}$'.format(COURSE_ID_PATTERN),
        users_views.UsersCoursesDetail.as_view(), name='users-courses-detail'),
    url(
        r'^(?P<user_id>[a-zA-Z0-9]+)/courses/*$',
        transaction.non_atomic_requests(users_views.UsersCoursesList.as_view()),
        name='users-courses-list'
    ),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/groups/*$', users_views.UsersGroupsList.as_view(), name='users-groups-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/groups/(?P<group_id>[0-9]+)$',
        users_views.UsersGroupsDetail.as_view(), name='users-groups-detail'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/preferences$',
        users_views.UsersPreferences.as_view(), name='users-preferences-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/preferences/(?P<preference_id>[a-zA-Z0-9_]+)$',
        users_views.UsersPreferencesDetail.as_view(), name='users-preferences-detail'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/organizations/$',
        users_views.UsersOrganizationsList.as_view(), name='users-organizations-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/roles/(?P<role>[a-z_]+)/courses/{0}$'.format(COURSE_ID_PATTERN),
        users_views.UsersRolesCoursesDetail.as_view(), name='users-roles-courses-detail'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/roles/*$', users_views.UsersRolesList.as_view(), name='users-roles-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/workgroups/$',
        users_views.UsersWorkgroupsList.as_view(), name='users-workgroups-list'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/notifications/(?P<msg_id>[0-9]+)/$',
        users_views.UsersNotificationsDetail.as_view(), name='users-notifications-detail'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)$', users_views.UsersDetail.as_view(), name='apimgr-users-detail'),
    url(r'/*$^', users_views.UsersList.as_view(), name='apimgr-users-list'),
    url(r'mass-details/$', users_views.MassUsersDetailsList.as_view(), name='apimgr-mass-users-detail'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/courses/progress',
        users_views.UsersCourseProgressList.as_view(), name='users-courses-progress'),
    url(r'^integration-test-users/$', users_views.UsersListWithEnrollment.as_view(), name='integration-test-users'),
    url(r'^(?P<user_id>[a-zA-Z0-9]+)/attributes/',
        users_views.ClientSpecificAttributesView.as_view(), name='users-attributes'),
    url(r'validate-token/$', users_views.TokenBasedUserDetails.as_view(),
        name='validate-bearer-token'),
    url(r'anonymous_id/$', users_views.UsersAnonymousId.as_view(),
        name='user-anonymous-id'),
]
urlpatterns = format_suffix_patterns(urlpatterns)
