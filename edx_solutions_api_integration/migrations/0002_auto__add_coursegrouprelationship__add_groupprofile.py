# -*- coding: utf-8 -*-
# pylint: disable=invalid-name, missing-docstring, unused-argument, unused-import, line-too-long
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding model 'CourseGroupRelationship'
        db.create_table('edx_solutions_api_integration_coursegrouprelationship', (
            ('id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('course_id', self.gf('django.db.models.fields.CharField')(max_length=255, db_index=True)),
            ('group', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['auth.Group'])),
        ))
        db.send_create_signal('edx_solutions_api_integration', ['CourseGroupRelationship'])

        # Adding model 'GroupProfile'
        db.create_table('auth_groupprofile', (
            ('id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('group', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['auth.Group'])),
            ('group_type', self.gf('django.db.models.fields.CharField')(max_length=32, null=True, db_index=True)),
            ('data', self.gf('django.db.models.fields.TextField')(blank=True)),
        ))
        db.send_create_signal('edx_solutions_api_integration', ['GroupProfile'])

    def backwards(self, orm):
        # Deleting model 'CourseGroupRelationship'
        db.delete_table('edx_solutions_api_integration_coursegrouprelationship')

        # Deleting model 'GroupProfile'
        db.delete_table('auth_groupprofile')

    models = {
        'edx_solutions_api_integration.coursegrouprelationship': {
            'Meta': {'object_name': 'CourseGroupRelationship'},
            'course_id': ('django.db.models.fields.CharField', [], {'max_length': '255', 'db_index': 'True'}),
            'group': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['auth.Group']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        'edx_solutions_api_integration.groupprofile': {
            'Meta': {'object_name': 'GroupProfile', 'db_table': "'auth_groupprofile'"},
            'data': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'group': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['auth.Group']"}),
            'group_type': ('django.db.models.fields.CharField', [], {'max_length': '32', 'null': 'True', 'db_index': 'True'}),  # pylint: disable=C0301
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        'edx_solutions_api_integration.grouprelationship': {
            'Meta': {'object_name': 'GroupRelationship'},
            'group': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['auth.Group']", 'unique': 'True', 'primary_key': 'True'}),  # pylint: disable=C0301
            'name': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            'parent_group': ('django.db.models.fields.related.ForeignKey', [], {'default': '0', 'related_name': "'child_groups'", 'null': 'True', 'blank': 'True', 'to': "orm['edx_solutions_api_integration.GroupRelationship']"}),  # pylint: disable=C0301
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'record_date_created': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime(2014, 4, 21, 0, 0)'}),  # pylint: disable=C0301
            'record_date_modified': ('django.db.models.fields.DateTimeField', [], {'auto_now': 'True', 'blank': 'True'})
        },
        'edx_solutions_api_integration.linkedgrouprelationship': {
            'Meta': {'object_name': 'LinkedGroupRelationship'},
            'from_group_relationship': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'from_group_relationships'", 'to': "orm['edx_solutions_api_integration.GroupRelationship']"}),  # pylint: disable=C0301
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'record_date_created': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime(2014, 4, 21, 0, 0)'}),  # pylint: disable=C0301
            'record_date_modified': ('django.db.models.fields.DateTimeField', [], {'auto_now': 'True', 'blank': 'True'}),  # pylint: disable=C0301
            'to_group_relationship': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'to_group_relationships'", 'to': "orm['edx_solutions_api_integration.GroupRelationship']"})  # pylint: disable=C0301
        },
        'auth.group': {
            'Meta': {'object_name': 'Group'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '80'}),
            'permissions': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['auth.Permission']", 'symmetrical': 'False', 'blank': 'True'})  # pylint: disable=C0301
        },
        'auth.permission': {
            'Meta': {'ordering': "('content_type__app_label', 'content_type__model', 'codename')", 'unique_together': "(('content_type', 'codename'),)", 'object_name': 'Permission'},  # pylint: disable=C0301
            'codename': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['contenttypes.ContentType']"}),  # pylint: disable=C0301
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'})
        },
        'contenttypes.contenttype': {
            'Meta': {'ordering': "('name',)", 'unique_together': "(('app_label', 'model'),)", 'object_name': 'ContentType', 'db_table': "'django_content_type'"},  # pylint: disable=C0301
            'app_label': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'model': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'})
        }
    }

    complete_apps = ['edx_solutions_api_integration']
