from edx_solutions_organizations.models import Organization
from mobileapps.serializers import BasicMobileAppSerializer, BasicThemeSerializer
from rest_framework import serializers
from edx_solutions_organizations.serializers import BasicOrganizationSerializer


class MobileOrganizationSerializer(BasicOrganizationSerializer):
    """ Serializer for Organization with mobile apps and themes data """
    mobile_apps = BasicMobileAppSerializer(many=True)
    theme = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ('url', 'id', 'name', 'display_name', 'contact_name', 'contact_email', 'contact_phone',
                  'created', 'modified', 'theme', 'mobile_apps')

    def get_theme(self, organization):
        theme = [theme for theme in organization.theme.all() if theme.active]
        if len(theme) > 0:
            serializer = BasicThemeSerializer(theme[0])
            return serializer.data
