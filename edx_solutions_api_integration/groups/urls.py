""" Groups API URI specification """
from django.conf import settings
from django.conf.urls import url
from edx_solutions_api_integration.groups import views as groups_views
from rest_framework.urlpatterns import format_suffix_patterns

COURSE_ID_PATTERN = settings.COURSE_ID_PATTERN
GROUP_ID_PATTERN = r'(?P<group_id>[0-9]+)'

urlpatterns = [
    url(r'/*$^', groups_views.GroupsList.as_view()),
    url(r'^{}/courses/{}$'.format(GROUP_ID_PATTERN, COURSE_ID_PATTERN), groups_views.GroupsCoursesDetail.as_view()),
    url(r'^{}/courses/*$'.format(GROUP_ID_PATTERN), groups_views.GroupsCoursesList.as_view()),
    url(r'^{}/organizations/*$'.format(GROUP_ID_PATTERN), groups_views.GroupsOrganizationsList.as_view()),
    url(r'^{}/workgroups/*$'.format(GROUP_ID_PATTERN), groups_views.GroupsWorkgroupsList.as_view()),
    url(r'^{}/users/*$'.format(GROUP_ID_PATTERN), groups_views.GroupsUsersList.as_view()),
    url(r'^{}/users/(?P<user_id>[0-9]+)$'.format(GROUP_ID_PATTERN), groups_views.GroupsUsersDetail.as_view()),
    url(r'^{}/groups/*$'.format(GROUP_ID_PATTERN), groups_views.GroupsGroupsList.as_view()),
    url(r'^{}/groups/(?P<related_group_id>[0-9]+)$'.format(GROUP_ID_PATTERN),
        groups_views.GroupsGroupsDetail.as_view()),
    url(r'^{}$'.format(GROUP_ID_PATTERN), groups_views.GroupsDetail.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
