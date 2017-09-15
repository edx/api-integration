# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def forwards_func(apps, schema_editor):
    """
    Creates index on city field of `auth_userprofile` table.
    :param apps:
    :param schema_editor:
    :return:
    """
    schema_editor.execute("CREATE INDEX cities_index ON `auth_userprofile` (city(255)) USING BTREE;")


def reverse_func(apps, schema_editor):
    """
    Removes index on city field of `auth_userprofile` table.
    :param apps:
    :param schema_editor:
    :return:
    """
    schema_editor.execute("DROP INDEX cities_index on `auth_userprofile`;")


class Migration(migrations.Migration):

    dependencies = [
        ('edx_solutions_api_integration', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]
