# -*- coding: utf-8 -*-
# pylint: disable=invalid-name, missing-docstring, unused-argument, unused-import, line-too-long
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Deleting field 'GroupRelationship.record_date_created'
        db.delete_column('edx_solutions_api_integration_grouprelationship', 'record_date_created')

        # Deleting field 'GroupRelationship.record_date_modified'
        db.delete_column('edx_solutions_api_integration_grouprelationship', 'record_date_modified')

        # Adding field 'GroupRelationship.created'
        db.add_column('edx_solutions_api_integration_grouprelationship', 'created',
                      self.gf('model_utils.fields.AutoCreatedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'GroupRelationship.modified'
        db.add_column('edx_solutions_api_integration_grouprelationship', 'modified',
                      self.gf('model_utils.fields.AutoLastModifiedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'CourseGroupRelationship.created'
        db.add_column('edx_solutions_api_integration_coursegrouprelationship', 'created',
                      self.gf('model_utils.fields.AutoCreatedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'CourseGroupRelationship.modified'
        db.add_column('edx_solutions_api_integration_coursegrouprelationship', 'modified',
                      self.gf('model_utils.fields.AutoLastModifiedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'CourseGroupRelationship.record_active'
        db.add_column('edx_solutions_api_integration_coursegrouprelationship', 'record_active',
                      self.gf('django.db.models.fields.BooleanField')(default=True),
                      keep_default=False)

        # Adding field 'GroupProfile.created'
        db.add_column('auth_groupprofile', 'created',
                      self.gf('model_utils.fields.AutoCreatedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'GroupProfile.modified'
        db.add_column('auth_groupprofile', 'modified',
                      self.gf('model_utils.fields.AutoLastModifiedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'GroupProfile.record_active'
        db.add_column('auth_groupprofile', 'record_active',
                      self.gf('django.db.models.fields.BooleanField')(default=True),
                      keep_default=False)

        # Deleting field 'LinkedGroupRelationship.record_date_created'
        db.delete_column('edx_solutions_api_integration_linkedgrouprelationship', 'record_date_created')

        # Deleting field 'LinkedGroupRelationship.record_date_modified'
        db.delete_column('edx_solutions_api_integration_linkedgrouprelationship', 'record_date_modified')

        # Adding field 'LinkedGroupRelationship.created'
        db.add_column('edx_solutions_api_integration_linkedgrouprelationship', 'created',
                      self.gf('model_utils.fields.AutoCreatedField')(default=datetime.datetime.now),
                      keep_default=False)

        # Adding field 'LinkedGroupRelationship.modified'
        db.add_column('edx_solutions_api_integration_linkedgrouprelationship', 'modified',
                      self.gf('model_utils.fields.AutoLastModifiedField')(default=datetime.datetime.now),
                      keep_default=False)

    def backwards(self, orm):
        # Adding field 'GroupRelationship.record_date_created'
        db.add_column('edx_solutions_api_integration_grouprelationship', 'record_date_created',
                      self.gf('django.db.models.fields.DateTimeField')(default=datetime.datetime(2014, 4, 30, 0, 0)),
                      keep_default=False)

        # Adding field 'GroupRelationship.record_date_modified'
        db.add_column('edx_solutions_api_integration_grouprelationship', 'record_date_modified',
                      self.gf('django.db.models.fields.DateTimeField')(auto_now=True, default=datetime.datetime(2014, 5, 7, 0, 0), blank=True),  # pylint: disable=C0301
                      keep_default=False)

        # Deleting field 'GroupRelationship.created'
        db.delete_column('edx_solutions_api_integration_grouprelationship', 'created')

        # Deleting field 'GroupRelationship.modified'
        db.delete_column('edx_solutions_api_integration_grouprelationship', 'modified')

        # Deleting field 'CourseGroupRelationship.created'
        db.delete_column('edx_solutions_api_integration_coursegrouprelationship', 'created')

        # Deleting field 'CourseGroupRelationship.modified'
        db.delete_column('edx_solutions_api_integration_coursegrouprelationship', 'modified')

        # Deleting field 'CourseGroupRelationship.record_active'
        db.delete_column('edx_solutions_api_integration_coursegrouprelationship', 'record_active')

        # Deleting field 'GroupProfile.created'
        db.delete_column('auth_groupprofile', 'created')

        # Deleting field 'GroupProfile.modified'
        db.delete_column('auth_groupprofile', 'modified')

        # Deleting field 'GroupProfile.record_active'
        db.delete_column('auth_groupprofile', 'record_active')

        # Adding field 'LinkedGroupRelationship.record_date_created'
        db.add_column('edx_solutions_api_integration_linkedgrouprelationship', 'record_date_created',
                      self.gf('django.db.models.fields.DateTimeField')(default=datetime.datetime(2014, 4, 30, 0, 0)),
                      keep_default=False)

        # Adding field 'LinkedGroupRelationship.record_date_modified'
        db.add_column('edx_solutions_api_integration_linkedgrouprelationship', 'record_date_modified',
                      self.gf('django.db.models.fields.DateTimeField')(auto_now=True, default=datetime.datetime(2014, 5, 7, 0, 0), blank=True),  # pylint: disable=C0301
                      keep_default=False)

        # Deleting field 'LinkedGroupRelationship.created'
        db.delete_column('edx_solutions_api_integration_linkedgrouprelationship', 'created')

        # Deleting field 'LinkedGroupRelationship.modified'
        db.delete_column('edx_solutions_api_integration_linkedgrouprelationship', 'modified')

    models = {
        'edx_solutions_api_integration.coursegrouprelationship': {
            'Meta': {'object_name': 'CourseGroupRelationship'},
            'course_id': ('django.db.models.fields.CharField', [], {'max_length': '255', 'db_index': 'True'}),
            'created': ('model_utils.fields.AutoCreatedField', [], {'default': 'datetime.datetime.now'}),
            'group': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['auth.Group']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'modified': ('model_utils.fields.AutoLastModifiedField', [], {'default': 'datetime.datetime.now'}),
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'})
        },
        'edx_solutions_api_integration.groupprofile': {
            'Meta': {'object_name': 'GroupProfile', 'db_table': "'auth_groupprofile'"},
            'created': ('model_utils.fields.AutoCreatedField', [], {'default': 'datetime.datetime.now'}),
            'data': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'group': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['auth.Group']"}),
            'group_type': ('django.db.models.fields.CharField', [], {'max_length': '32', 'null': 'True', 'db_index': 'True'}),  # pylint: disable=C0301
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'modified': ('model_utils.fields.AutoLastModifiedField', [], {'default': 'datetime.datetime.now'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '255', 'null': 'True', 'blank': 'True'}),
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'})
        },
        'edx_solutions_api_integration.grouprelationship': {
            'Meta': {'object_name': 'GroupRelationship'},
            'created': ('model_utils.fields.AutoCreatedField', [], {'default': 'datetime.datetime.now'}),
            'group': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['auth.Group']", 'unique': 'True', 'primary_key': 'True'}),  # pylint: disable=C0301
            'modified': ('model_utils.fields.AutoLastModifiedField', [], {'default': 'datetime.datetime.now'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            'parent_group': ('django.db.models.fields.related.ForeignKey', [], {'default': '0', 'related_name': "'child_groups'", 'null': 'True', 'blank': 'True', 'to': "orm['edx_solutions_api_integration.GroupRelationship']"}),  # pylint: disable=C0301
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'})
        },
        'edx_solutions_api_integration.linkedgrouprelationship': {
            'Meta': {'object_name': 'LinkedGroupRelationship'},
            'created': ('model_utils.fields.AutoCreatedField', [], {'default': 'datetime.datetime.now'}),
            'from_group_relationship': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'from_group_relationships'", 'to': "orm['edx_solutions_api_integration.GroupRelationship']"}),  # pylint: disable=C0301
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'modified': ('model_utils.fields.AutoLastModifiedField', [], {'default': 'datetime.datetime.now'}),
            'record_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
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
