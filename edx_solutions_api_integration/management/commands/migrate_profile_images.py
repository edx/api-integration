"""
Management command to migrate existing apros profile images to open edx profile images feature
"""

import logging
import urllib2 as urllib
import io

from contextlib import closing
from django.core.management.base import BaseCommand
from django.conf import settings
from openedx.core.djangoapps.profile_images.images import create_profile_images
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names, set_has_profile_image

from edx_solutions_api_integration.models import APIUser as User
from boto.s3.connection import S3Connection

log = logging.getLogger(__name__)


class MigrateProfileImagesCommand(BaseCommand):

    @staticmethod
    def get_bucket_connection(bucket_name):
        conn = S3Connection()
        bucket = conn.get_bucket(bucket_name)
        return bucket

    @staticmethod
    def get_file_with_key(bucket, key_str, filename):
        bucket_key = bucket.get_key(key_str)
        bucket_key.get_contents_to_filename(filename)

    @staticmethod
    def get_file_url(bucket, key_str):
        bucket_key = bucket.get_key(key_str)
        return bucket_key.generate_url(expires_in=300) if bucket_key else None

    def handle(self, *args, **options):
        log.info("Starting Migration of Profile Images")
        apros_bucket = MigrateProfileImagesCommand.get_bucket_connection(settings.PROFILE_IMAGE_BUCKET_NAME)

        if apros_bucket:
            log.info("Bucket name is " + settings.PROFILE_IMAGE_BUCKET_NAME)
            for user in User.objects.exclude(profile__avatar_url__isnull=True):
                image_key = (user.profile.avatar_url.split('/')[-2]) + '/' + (user.profile.avatar_url.split('/')[-1])
                image_url = MigrateProfileImagesCommand.get_file_url(apros_bucket, image_key)

                log.info("Get image_url " + image_url + " of " + user.username)
                if image_url:
                    with closing(urllib.urlopen(image_url)) as fd:
                        image_file = io.BytesIO(fd.read())

                    # generate profile pic and thumbnails and store them
                    profile_image_names = get_profile_image_names(user.username)
                    create_profile_images(image_file, profile_image_names)

                    log.info("Profile image updated of " + user.username)
