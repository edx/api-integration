"""
Tests for rename_api_manager_app command
"""
import mock
from datetime import datetime

from south.db import db
from south.models import MigrationHistory
from django.db import models, connection
from django.test import TestCase
from django.core.management import call_command
from django.contrib.contenttypes.models import ContentType


class RenameApiManagerAppTests(TestCase):
    """
    Test suite for renaming api_manager app related database tables
    """
    def setUp(self):
        super(RenameApiManagerAppTests, self).setUp()

        self.old_appname = 'old_dummy_app'
        self.new_appname = 'new_dummy_app'
        self.table_names_mappings = {
            'old_fake_table': 'new_fake_table',
            'old_dummy_table': 'new_dummy_table'
        }

        self.old_app_migration_history = MigrationHistory.objects.create(
            app_name=self.old_appname,
            migration='0001_initial',
            applied=datetime.now()
        )
        self.old_app_content_type = ContentType.objects.create(
            app_label=self.old_appname,
            name='dummy model',
            model='dummymodel'
        )

        for table_name in self.table_names_mappings.keys():
            db.create_table(table_name, (
                ('id', models.AutoField(primary_key=True)),
                ('name', models.CharField(unique=True, max_length=50)),
            ))

    def tearDown(self):
        super(RenameApiManagerAppTests, self).tearDown()

        for table_name in self.table_names_mappings.values():
            db.delete_table(table_name)

    def table_exists(self, table_name):
        """
        Checks if table exists in database
        """
        tables = connection.introspection.table_names()

        return table_name in tables

    def test_rename_api_manager_app(self):
        """
        Test the api_manager renaming
        """
        for table_name in self.table_names_mappings.keys():
            self.assertEqual(self.table_exists(table_name), True)

        self.assertEqual(MigrationHistory.objects.filter(app_name=self.old_appname).count(), 1)
        self.assertEqual(MigrationHistory.objects.filter(app_name=self.new_appname).count(), 0)
        self.assertEqual(ContentType.objects.filter(app_label=self.old_appname).count(), 1)
        self.assertEqual(ContentType.objects.filter(app_label=self.new_appname).count(), 0)

        with mock.patch('edx_solutions_api_integration.management.commands.rename_api_manager_app.get_table_name_mappings') as patched_get_table_name_mappings, \
             mock.patch('edx_solutions_api_integration.management.commands.rename_api_manager_app.get_old_appname') as patched_get_old_appname, \
             mock.patch('edx_solutions_api_integration.management.commands.rename_api_manager_app.get_new_appname') as patched_get_new_appname:  # pylint: disable=line-too-long

            patched_get_old_appname.return_value = self.old_appname
            patched_get_new_appname.return_value = self.new_appname
            patched_get_table_name_mappings.return_value = self.table_names_mappings
            call_command('rename_api_manager_app')

        for table_name in self.table_names_mappings.keys():
            self.assertEqual(self.table_exists(table_name), False)

        for table_name in self.table_names_mappings.values():
            self.assertEqual(self.table_exists(table_name), True)

        self.assertEqual(MigrationHistory.objects.filter(app_name=self.old_appname).count(), 0)
        self.assertEqual(MigrationHistory.objects.filter(app_name=self.new_appname).count(), 1)
        self.assertEqual(ContentType.objects.filter(app_label=self.old_appname).count(), 0)
        self.assertEqual(ContentType.objects.filter(app_label=self.new_appname).count(), 1)
