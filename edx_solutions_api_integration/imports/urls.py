"""
Import URLs.
"""

from rest_framework.routers import DefaultRouter

from edx_solutions_api_integration.imports import views

router = DefaultRouter()
router.register(r'participants', views.ImportParticipantsViewSet, base_name='import-participants')
urlpatterns = router.urls
