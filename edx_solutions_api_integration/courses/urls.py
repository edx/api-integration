"""
Courses API URI specification
The order of the URIs really matters here, due to the slash characters present in the identifiers
"""
from django.conf import settings
from django.conf.urls import url
from edx_solutions_api_integration.courses import views as courses_views
from rest_framework.urlpatterns import format_suffix_patterns

CONTENT_ID_PATTERN = r'(?P<content_id>(?:i4x://?[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+))'
COURSE_ID_PATTERN = settings.COURSE_ID_PATTERN

urlpatterns = [
    url(r'^{}/content/{}/groups/(?P<group_id>[0-9]+)$'.format(COURSE_ID_PATTERN, CONTENT_ID_PATTERN),
        courses_views.CourseContentGroupsDetail.as_view()),
    url(r'^{}/content/{}/groups/*$'.format(COURSE_ID_PATTERN, CONTENT_ID_PATTERN),
        courses_views.CourseContentGroupsList.as_view()),
    url(r'^{}/content/{}/children/*$'.format(COURSE_ID_PATTERN, CONTENT_ID_PATTERN),
        courses_views.CourseContentList.as_view()),
    url(r'^{}/content/{}/users/*$'.format(COURSE_ID_PATTERN, CONTENT_ID_PATTERN),
        courses_views.CourseContentUsersList.as_view()),
    url(r'^{}/content/{}$'.format(COURSE_ID_PATTERN, CONTENT_ID_PATTERN),
        courses_views.CourseContentDetail.as_view()),
    url(r'^{}/content/*$'.format(COURSE_ID_PATTERN), courses_views.CourseContentList.as_view()),
    url(r'^{}/groups/(?P<group_id>[0-9]+)$'.format(COURSE_ID_PATTERN), courses_views.CoursesGroupsDetail.as_view()),
    url(r'^{}/groups/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesGroupsList.as_view()),
    url(r'^{}/enrollment_count/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesEnrollmentCount.as_view()),
    url(r'^{}/average_scores/(?P<score_type>\w+)/*$'.format(COURSE_ID_PATTERN),
        courses_views.CourseAverageScores.as_view()),
    url(r'^{}/overview/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesOverview.as_view()),
    url(r'^{}/completions/*$'.format(COURSE_ID_PATTERN),
        courses_views.CompletionList.as_view(), name='completion-list'),
    url(r'^{}/static_tabs/(?P<tab_id>[a-zA-Z0-9_+\s\/:-]+)$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesStaticTabsDetail.as_view()),
    url(r'^{}/static_tabs/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesStaticTabsList.as_view()),
    url(r'^{}/projects/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesProjectList.as_view(), name='courseproject-list'),
    url(r'^{}/metrics/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesMetrics.as_view(), name='course-metrics'),
    url(r'^{}/time-series-metrics/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesTimeSeriesMetrics.as_view(), name='course-time-series-metrics'),
    url(r'^{}/metrics/cities/$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesMetricsCities.as_view(), name='courses-cities-metrics'),
    url(r'^{}/metrics/completions/leaders/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesMetricsCompletionsLeadersList.as_view(), name='course-metrics-completions-leaders'),
    url(r'^{}/metrics/grades/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesMetricsGradesList.as_view()),
    url(r'^{}/metrics/grades/leaders/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesMetricsGradesLeadersList.as_view(), name='course-metrics-grades-leaders'),
    url(r'^{}/metrics/social/$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesMetricsSocial.as_view(), name='courses-social-metrics'),
    url(r'^{}/metrics/social/leaders/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesMetricsSocialLeadersList.as_view(), name='course-metrics-social-leaders'),
    url(r'^{}/metrics/leaders/*$'.format(COURSE_ID_PATTERN),
        courses_views.CourseMetricsLeaders.as_view(), name='course-metrics-leaders'),
    url(r'^{}/roles/(?P<role>[a-z_]+)/users/(?P<user_id>[0-9]+)*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesRolesUsersDetail.as_view(), name='courses-roles-users-detail'),
    url(r'^{}/roles/*$'.format(COURSE_ID_PATTERN),
        courses_views.CoursesRolesList.as_view(), name='courses-roles-list'),
    url(r'^{}/updates/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesUpdates.as_view()),
    url(r'^{}/users/(?P<user_id>[0-9]+)$'.format(COURSE_ID_PATTERN), courses_views.CoursesUsersDetail.as_view()),
    url(r'^{}/users/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesUsersList.as_view()),
    url(r'^{}/engagement_summary/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesEngagementSummary.as_view()),
    url(r'^{}/users/passed$'.format(COURSE_ID_PATTERN), courses_views.CoursesUsersPassedList.as_view()),
    url(r'^{}/workgroups/*$'.format(COURSE_ID_PATTERN), courses_views.CoursesWorkgroupsList.as_view()),
    url(r'^{}/navigation/{}$'.format(COURSE_ID_PATTERN, settings.USAGE_KEY_PATTERN),
        courses_views.CourseNavView.as_view()),
    url(r'^{}$'.format(COURSE_ID_PATTERN), courses_views.CoursesDetail.as_view(), name='course-detail'),
    url(r'tree$', courses_views.CoursesTree.as_view()),
    url(r'gw_map$', courses_views.CourseGWMap.as_view()),
    url(r'convert_ooyala_to_bcove/$', courses_views.OoyalaToBcoveConversion.as_view()),
    url(r'get_asset_urls/$', courses_views.AssetURLs.as_view()),
    url(r'^$', courses_views.CoursesList.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
