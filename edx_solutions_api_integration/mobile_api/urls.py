"""
URLs for edx-solutions mobile API
"""
from django.conf.urls import patterns, url
from edx_solutions_api_integration.courses.urls import COURSE_ID_PATTERN
from edx_solutions_api_integration.mobile_api import views as mobile_views


urlpatterns = patterns(
    '',
    url(r'^users/organizations/', mobile_views.MobileUsersOrganizationsList.as_view(), name='mobile-users-orgs-list'),
    url(r'^users/courses/progress', mobile_views.MobileUsersCourseProgressList.as_view(),
        name='mobile-users-courses-progress'),
    url(r'^courses/{0}/overview'.format(COURSE_ID_PATTERN), mobile_views.MobileCoursesOverview.as_view(),
        name='mobile-courses-overview'),
    url(r'^users/courses/{0}'.format(COURSE_ID_PATTERN), mobile_views.MobileUsersCoursesDetail.as_view(),
        name='mobile-users-courses-detail'),
)
