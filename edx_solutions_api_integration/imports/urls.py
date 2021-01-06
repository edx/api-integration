"""
Import URLs.
"""

from edx_solutions_api_integration.imports import views
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'participants', views.ImportParticipantsViewSet, base_name='import-participants')
urlpatterns = router.urls
