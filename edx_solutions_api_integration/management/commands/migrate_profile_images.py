"""
Management command to migrate profile images from s3 bucket to open edx profile images storage
./manage.py lms migrate_profile_images  --settings=aws --aws-access-key="x" --aws-access-secret="x" --bucket-name="b"
"""

import datetime
import logging
from contextlib import closing
from io import BytesIO
from optparse import make_option
from urllib.request import urlopen

from boto.exception import S3ResponseError
from boto.s3.connection import S3Connection
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import utc
from edx_solutions_api_integration.models import APIUser as User
from openedx.core.djangoapps.profile_images.images import create_profile_images
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names
from student.models import UserProfile

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to migrate profile images from s3 bucket to open edx profile images storage
    """
    help = 'Migrates profile images from s3 bucket to open edx profile images storage'
    def add_arguments(self, parser):
        parser.add_argument(
            "--aws-access-key",
            dest="aws_access_key",
            help="AWS access key",
        ),
        parser.add_argument(
            "--aws-access-secret",
            dest="aws_access_secret",
            help="AWS access secret",
        ),
        parser.add_argument(
            "--bucket-name",
            dest="bucket_name",
            help="Name of source s3 bucket",
        ),

    @staticmethod
    def get_bucket_connection(aws_access_key, aws_access_secret, bucket_name):
        conn = S3Connection(aws_access_key, aws_access_secret)
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
        if not options.get('aws_access_key'):
            raise CommandError("migrate_profile_images command requires AWS access key in --aws-access-key")

        if not options.get('aws_access_secret'):
            raise CommandError("migrate_profile_images command requires AWS access secret in --aws-access-secret")

        if not options.get('bucket_name'):
            raise CommandError("migrate_profile_images command requires name of source s3 bucket in --bucket-name")

        aws_access_key = options.get('aws_access_key')
        aws_access_secret = options.get('aws_access_secret')
        bucket_name = options.get('bucket_name')

        try:
            log.info("Starting Migration of Profile Images from %s", bucket_name)
            bucket = Command.get_bucket_connection(aws_access_key, aws_access_secret, bucket_name)
            for user in User.objects.exclude(profile__avatar_url__isnull=True).exclude(profile__avatar_url__exact=''):
                try:
                    image_key = (user.profile.avatar_url.split('/')[-2]) + '/' + (user.profile.avatar_url.split('/')[-1])
                    image_url = Command.get_file_url(bucket, image_key)
                except IndexError:
                    log.info('Unknown avatar url(%s) for %s', user.profile.avatar_url, user.username)
                    continue

                log.info("Get image_url %s of %s", image_url, user.username)
                if image_url:
                    with closing(urlopen(image_url)) as fd:
                        image_file = BytesIO(fd.read())

                    # generate profile pic and thumbnails and store them
                    profile_image_names = get_profile_image_names(user.username)
                    create_profile_images(image_file, profile_image_names)

                    # update the user account to reflect that a profile image is available.
                    uploaded_at = datetime.datetime.utcnow().replace(tzinfo=utc)
                    UserProfile.objects.filter(user=user).update(profile_image_uploaded_at=uploaded_at)
                    log.info("Profile image updated of %s with upload timestamp %s", user.username, uploaded_at)
                else:
                    log.info("Profile image for username %s not found in source bucket", bucket_name)

        except S3ResponseError as ex:
            log.info("Unable to connect to bucket %s. %s", bucket_name, ex.message)
