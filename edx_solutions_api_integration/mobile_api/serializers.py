from edx_solutions_organizations.models import Organization
from mobileapps.serializers import MobileAppSerializer, ThemeSerializer
from rest_framework import serializers
from edx_solutions_organizations.serializers import BasicOrganizationSerializer


class MobileOrganizationSerializer(BasicOrganizationSerializer):
    """ Serializer for Organization with mobile apps and themes data """
    mobile_apps = MobileAppSerializer(many=True)
    theme = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = BasicOrganizationSerializer.Meta.fields + ('theme', 'mobile_apps')

    def get_theme(self, organization):
        theme = [theme for theme in organization.theme.all() if theme.active]
        if len(theme) > 0:
            serializer = ThemeSerializer(theme[0])
            return serializer.data
