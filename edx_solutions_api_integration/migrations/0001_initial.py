# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.utils.timezone
import model_utils.fields


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0006_require_contenttypes_0002'),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseContentGroupRelationship',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('course_id', models.CharField(max_length=255, db_index=True)),
                ('content_id', models.CharField(max_length=255, db_index=True)),
                ('record_active', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='CourseGroupRelationship',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('course_id', models.CharField(max_length=255, db_index=True)),
                ('record_active', models.BooleanField(default=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='GroupProfile',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('group_type', models.CharField(max_length=32, null=True, db_index=True)),
                ('name', models.CharField(max_length=255, null=True, blank=True)),
                ('data', models.TextField(blank=True)),
                ('record_active', models.BooleanField(default=True)),
            ],
            options={
                'db_table': 'auth_groupprofile',
            },
        ),
        migrations.CreateModel(
            name='GroupRelationship',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('group', models.OneToOneField(primary_key=True, serialize=False, to='auth.Group')),
                ('name', models.CharField(max_length=255)),
                ('record_active', models.BooleanField(default=True)),
                ('parent_group', models.ForeignKey(related_name='child_groups', default=0, blank=True, to='edx_solutions_api_integration.GroupRelationship', null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='LinkedGroupRelationship',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('record_active', models.BooleanField(default=True)),
                ('from_group_relationship', models.ForeignKey(related_name='from_group_relationships', verbose_name=b'From Group', to='edx_solutions_api_integration.GroupRelationship')),
                ('to_group_relationship', models.ForeignKey(related_name='to_group_relationships', verbose_name=b'To Group', to='edx_solutions_api_integration.GroupRelationship')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='APIUser',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('auth.user',),
        ),
        migrations.AddField(
            model_name='groupprofile',
            name='group',
            field=models.OneToOneField(to='auth.Group'),
        ),
        migrations.AddField(
            model_name='coursegrouprelationship',
            name='group',
            field=models.ForeignKey(to='auth.Group'),
        ),
        migrations.AddField(
            model_name='coursecontentgrouprelationship',
            name='group_profile',
            field=models.ForeignKey(to='edx_solutions_api_integration.GroupProfile'),
        ),
        migrations.AlterUniqueTogether(
            name='coursecontentgrouprelationship',
            unique_together=set([('course_id', 'content_id', 'group_profile')]),
        ),
    ]
