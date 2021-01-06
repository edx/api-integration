"""
Tests test add company admin group management command
"""
import unittest
import uuid

from django.contrib.auth.models import Group
from edx_solutions_api_integration.management.commands import add_new_permissions
from edx_solutions_organizations.models import Organization
from opaque_keys.edx import locator
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestAddNewPermissions(ModuleStoreTestCase):
    """Tests adding new company admin role."""

    ORGANIZATIONS_NUM = 10
    def setUp(self, **kwargs):
        super().setUp()
        self.group_type = "permission"
        self.original_group_name = "mcka_role_company_admin"
        self.organizations = []

        for index in range(0, self.ORGANIZATIONS_NUM):
            self.organizations.append(Organization.objects.create(
                name="test_org_"+str(index),
                display_name="test_display_name_"+str(index),
                contact_name="test_contact_"+str(index),
                contact_email="test_email_"+str(index)+"@yopmail.com",
                contact_phone="0"+str(index)
            ))

    def test_existing_add_company_admin(self):
        """ Verify in case of existing company admin group"""

        mcka_company_admin = Group.objects.create(name=str(uuid.uuid4()))
        mcka_company_admin.name = '{:04d}: {}'.format(mcka_company_admin.id, self.original_group_name)
        mcka_company_admin.record_active = True
        mcka_company_admin.save()

        # Run the actual management command
        add_new_permissions.Command().handle()

        mcka_company_admin = Group.objects.filter(name__icontains=self.original_group_name)
        self.assertEqual(len(mcka_company_admin), 1)

        for organization in self.organizations:
            group = Group.objects.filter(id=mcka_company_admin[0].id, organizations=organization.id)
            self.assertEqual(len(group), 1)

    def test_clean_add_company_admin(self):
        """ Verify fresh company admin adding"""

        # Run the actual management command
        add_new_permissions.Command().handle()

        mcka_company_admin = Group.objects.filter(name__icontains=self.original_group_name)
        self.assertEqual(len(mcka_company_admin), 1)

        for organization in self.organizations:
            group = Group.objects.filter(id=mcka_company_admin[0].id, organizations=organization.id)
            self.assertEqual(len(group), 1)
