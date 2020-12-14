"""
URLs for edx-solutions mobile API
"""
from django.conf.urls import url
from edx_solutions_api_integration.courses.urls import COURSE_ID_PATTERN
from edx_solutions_api_integration.mobile_api import views as mobile_views

urlpatterns = [
    url(r'^users/organizations/',
        mobile_views.MobileUsersOrganizationsList.as_view(), name='mobile-users-orgs-list'),
    url(r'^users/courses/progress',
        mobile_views.MobileUsersCourseProgressList.as_view(), name='mobile-users-courses-progress'),
    url(r'^courses/{}/overview'.format(COURSE_ID_PATTERN),
        mobile_views.MobileCoursesOverview.as_view(), name='mobile-courses-overview'),
    url(r'^users/courses/{}'.format(COURSE_ID_PATTERN),
        mobile_views.MobileUsersCoursesDetail.as_view(), name='mobile-users-courses-detail'),
    url(r'^courses/{}/static_tabs/*$'.format(COURSE_ID_PATTERN),
        mobile_views.MobileCoursesStaticTabsList.as_view(), name='mobile-courses-static-tabs-list'),
    url(r'^courses/{}/static_tabs/(?P<tab_id>[a-zA-Z0-9_+\s\/:-]+)$'.format(COURSE_ID_PATTERN),
        mobile_views.MobileCoursesStaticTabsDetail.as_view(), name='mobile-courses-static-tabs-details'),
    url(r'^users/discussion_metrics/',
        mobile_views.MobileUsersDiscussionMetrics.as_view(), name='mobile-users-discussion-metrics'),
    url(r'^users/courses/grades',
        mobile_views.MobileUsersCoursesGrades.as_view(), name='mobile-users-courses-grades'),
]
