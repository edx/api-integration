""" Sessions API URI specification """
from django.conf.urls import url
from edx_solutions_api_integration.sessions import views as sessions_views
from rest_framework.urlpatterns import format_suffix_patterns

urlpatterns = [
    url(r'/*$^', sessions_views.SessionsList.as_view()),
    url(r'^(?P<session_id>[a-z0-9]+)/assets_token$', sessions_views.AssetsToken.as_view()),
    url(r'^(?P<session_id>[a-z0-9]+)$', sessions_views.SessionsDetail.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
