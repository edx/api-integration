""" Database ORM models managed by this Django app """
import logging
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.auth.hashers import make_password
from django.db import models
from django.utils import timezone

from opaque_keys.edx.django.models import CourseKeyField
from model_utils.models import TimeStampedModel
from .utils import is_int

AUDIT_LOG = logging.getLogger("audit")


class GroupRelationship(TimeStampedModel):
    """
    The GroupRelationship model contains information describing the relationships of a group,
    which allows us to utilize Django's user/group/permission
    models and features instead of rolling our own.
    """
    group = models.OneToOneField(Group, primary_key=True)
    name = models.CharField(max_length=255)
    parent_group = models.ForeignKey('self',
                                     related_name="child_groups",
                                     blank=True, null=True, default=0)
    linked_groups = models.ManyToManyField('self',
                                           through="LinkedGroupRelationship",
                                           symmetrical=False,
                                           related_name="linked_to+"),
    record_active = models.BooleanField(default=True)

    def add_linked_group_relationship(self, to_group_relationship, symmetrical=True):
        """ Create a new group-group relationship """
        relationship = LinkedGroupRelationship.objects.get_or_create(
            from_group_relationship=self,
            to_group_relationship=to_group_relationship)
        if symmetrical:
            # avoid recursion by passing `symm=False`
            to_group_relationship.add_linked_group_relationship(self, False)
        return relationship

    def remove_linked_group_relationship(self, to_group_relationship, symmetrical=True):
        """ Remove an existing group-group relationship """
        LinkedGroupRelationship.objects.filter(
            from_group_relationship=self,
            to_group_relationship=to_group_relationship).delete()
        if symmetrical:
            # avoid recursion by passing `symm=False`
            to_group_relationship.remove_linked_group_relationship(self, False)
        return

    def get_linked_group_relationships(self):
        """ Retrieve an existing group-group relationship """
        efferent_relationships = LinkedGroupRelationship.objects.filter(from_group_relationship=self)
        matching_relationships = efferent_relationships
        return matching_relationships

    def check_linked_group_relationship(self, relationship_to_check, symmetrical=False):
        """ Confirm the existence of a possibly-existing group-group relationship """
        query = dict(
            to_group_relationships__from_group_relationship=self,
            to_group_relationships__to_group_relationship=relationship_to_check,
        )
        if symmetrical:
            query.update(
                from_group_relationships__to_group_relationship=self,
                from_group_relationships__from_group_relationship=relationship_to_check,
            )
        return GroupRelationship.objects.filter(**query).exists()


class LinkedGroupRelationship(TimeStampedModel):
    """
    The LinkedGroupRelationship model manages self-referential two-way
    relationships between group entities via the GroupRelationship model.
    Specifying the intermediary table allows for the definition of additional
    relationship information
    """
    from_group_relationship = models.ForeignKey(GroupRelationship,
                                                related_name="from_group_relationships",
                                                verbose_name="From Group")
    to_group_relationship = models.ForeignKey(GroupRelationship,
                                              related_name="to_group_relationships",
                                              verbose_name="To Group")
    record_active = models.BooleanField(default=True)


class CourseGroupRelationship(TimeStampedModel):
    """
    The CourseGroupRelationship model contains information describing the
    link between a course and a group.  A typical use case for this table
    is to manage the courses for an XSeries or other sort of program.
    """
    course_id = models.CharField(max_length=255, db_index=True)
    group = models.ForeignKey(Group, db_index=True)
    record_active = models.BooleanField(default=True)


class GroupProfile(TimeStampedModel):
    """
    This table will provide additional tables regarding groups. This has a foreign key to
    the auth_groups table
    """

    class Meta(object):
        """
        Meta class for modifying things like table name
        """
        db_table = "auth_groupprofile"

    group = models.OneToOneField(Group, db_index=True)
    group_type = models.CharField(null=True, max_length=32, db_index=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    data = models.TextField(blank=True)  # JSON dictionary for generic key/value pairs
    record_active = models.BooleanField(default=True)


class CourseContentGroupRelationship(TimeStampedModel):
    """
    The CourseContentGroupRelationship model contains information describing the
    link between a particular courseware element (chapter, unit, video, etc.)
    and a group.  A typical use case for this table is to support the concept
    of a student workgroup for a given course, where the project is actually
    a Chapter courseware element.
    """
    course_id = models.CharField(max_length=255, db_index=True)
    content_id = models.CharField(max_length=255, db_index=True)
    group_profile = models.ForeignKey(GroupProfile, db_index=True)
    record_active = models.BooleanField(default=True)

    class Meta(object):
        """
        Mapping model to enable grouping of course content such as chapters
        """
        unique_together = ("course_id", "content_id", "group_profile")


class APIUserQuerySet(models.query.QuerySet):
    """ Custom QuerySet to modify id based lookup """
    def filter(self, *args, **kwargs):
        if 'id' in kwargs and not is_int(kwargs['id']):
            kwargs['anonymoususerid__anonymous_user_id'] = kwargs['id']
            del kwargs['id']
        return super(APIUserQuerySet, self).filter(*args, **kwargs)


class APIUserManager(models.Manager):
    """ Custom Manager """
    def get_queryset(self):
        return APIUserQuerySet(self.model)


class APIUser(User):
    """
    A proxy model for django's auth.User to add AnonymousUserId fallback
    support in User lookups
    """
    objects = APIUserManager()

    class Meta(object):
        """ Meta attribute to make this a proxy model"""
        proxy = True


class LeaderBoard(TimeStampedModel):
    """
    Model to store progress leaders against a course
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255)
    position = models.IntegerField()


class PasswordHistory(models.Model):
    """
    This model will keep track of past passwords that a user has used
    as well as providing contraints (e.g. can't reuse passwords)
    """
    class Meta(object):
        app_label = "student"

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    password = models.CharField(max_length=128)
    time_set = models.DateTimeField(default=timezone.now)

    def create(self, user):
        """
        This will copy over the current password, if any of the configuration has been turned on
        """
        if not (PasswordHistory.is_student_password_reuse_restricted() or
                PasswordHistory.is_staff_password_reuse_restricted() or
                PasswordHistory.is_password_reset_frequency_restricted() or
                PasswordHistory.is_staff_forced_password_reset_enabled() or
                PasswordHistory.is_student_forced_password_reset_enabled()):

            return

        self.user = user
        self.password = user.password
        self.save()

    @classmethod
    def is_student_password_reuse_restricted(cls):
        """
        Returns whether the configuration which limits password reuse has been turned on
        """
        min_diff_pw = settings.ADVANCED_SECURITY_CONFIG.get(
            'MIN_DIFFERENT_STUDENT_PASSWORDS_BEFORE_REUSE', 0
        )
        return min_diff_pw > 0

    @classmethod
    def is_staff_password_reuse_restricted(cls):
        """
        Returns whether the configuration which limits password reuse has been turned on
        """
        min_diff_pw = settings.ADVANCED_SECURITY_CONFIG.get(
            'MIN_DIFFERENT_STAFF_PASSWORDS_BEFORE_REUSE', 0
        )
        return min_diff_pw > 0

    @classmethod
    def is_password_reset_frequency_restricted(cls):
        """
        Returns whether the configuration which limits the password reset frequency has been turned on
        """
        min_days_between_reset = settings.ADVANCED_SECURITY_CONFIG.get(
            'MIN_TIME_IN_DAYS_BETWEEN_ALLOWED_RESETS'
        )
        return min_days_between_reset

    @classmethod
    def is_staff_forced_password_reset_enabled(cls):
        """
        Returns whether the configuration which forces password resets to occur has been turned on
        """
        min_days_between_reset = settings.ADVANCED_SECURITY_CONFIG.get(
            'MIN_DAYS_FOR_STAFF_ACCOUNTS_PASSWORD_RESETS'
        )
        return min_days_between_reset

    @classmethod
    def is_student_forced_password_reset_enabled(cls):
        """
        Returns whether the configuration which forces password resets to occur has been turned on
        """
        min_days_pw_reset = settings.ADVANCED_SECURITY_CONFIG.get(
            'MIN_DAYS_FOR_STUDENT_ACCOUNTS_PASSWORD_RESETS'
        )
        return min_days_pw_reset

    @classmethod
    def should_user_reset_password_now(cls, user):
        """
        Returns whether a password has 'expired' and should be reset. Note there are two different
        expiry policies for staff and students
        """
        days_before_password_reset = None
        if user.is_staff:
            if cls.is_staff_forced_password_reset_enabled():
                days_before_password_reset = \
                    settings.ADVANCED_SECURITY_CONFIG['MIN_DAYS_FOR_STAFF_ACCOUNTS_PASSWORD_RESETS']
        elif cls.is_student_forced_password_reset_enabled():
            days_before_password_reset = \
                settings.ADVANCED_SECURITY_CONFIG['MIN_DAYS_FOR_STUDENT_ACCOUNTS_PASSWORD_RESETS']

        if days_before_password_reset:
            history = PasswordHistory.objects.filter(user=user).order_by('-time_set')
            time_last_reset = None

            if history:
                # first element should be the last time we reset password
                time_last_reset = history[0].time_set
            else:
                # no history, then let's take the date the user joined
                time_last_reset = user.date_joined

            now = timezone.now()

            delta = now - time_last_reset

            return delta.days >= days_before_password_reset

        return False

    @classmethod
    def is_password_reset_too_soon(cls, user):
        """
        Verifies that the password is not getting reset too frequently
        """
        if not cls.is_password_reset_frequency_restricted():
            return False

        history = PasswordHistory.objects.filter(user=user).order_by('-time_set')

        if not history:
            return False

        now = timezone.now()

        delta = now - history[0].time_set

        return delta.days < settings.ADVANCED_SECURITY_CONFIG['MIN_TIME_IN_DAYS_BETWEEN_ALLOWED_RESETS']

    @classmethod
    def is_allowable_password_reuse(cls, user, new_password):
        """
        Verifies that the password adheres to the reuse policies
        """
        if user.is_staff and cls.is_staff_password_reuse_restricted():
            min_diff_passwords_required = \
                settings.ADVANCED_SECURITY_CONFIG['MIN_DIFFERENT_STAFF_PASSWORDS_BEFORE_REUSE']
        elif cls.is_student_password_reuse_restricted():
            min_diff_passwords_required = \
                settings.ADVANCED_SECURITY_CONFIG['MIN_DIFFERENT_STUDENT_PASSWORDS_BEFORE_REUSE']
        else:
            min_diff_passwords_required = 0

        # just limit the result set to the number of different
        # password we need
        history = PasswordHistory.objects.filter(user=user).order_by('-time_set')[:min_diff_passwords_required]

        for entry in history:

            # be sure to re-use the same salt
            # NOTE, how the salt is serialized in the password field is dependent on the algorithm
            # in pbkdf2_sha256 [LMS] it's the 3rd element, in sha1 [unit tests] it's the 2nd element
            hash_elements = entry.password.split('$')
            algorithm = hash_elements[0]
            if algorithm == 'pbkdf2_sha256':
                hashed_password = make_password(new_password, hash_elements[2])
            elif algorithm == 'sha1':
                hashed_password = make_password(new_password, hash_elements[1])
            else:
                # This means we got something unexpected. We don't want to throw an exception, but
                # log as an error and basically allow any password reuse
                AUDIT_LOG.error('''
                                Unknown password hashing algorithm "{0}" found in existing password
                                hash, password reuse policy will not be enforced!!!
                                '''.format(algorithm))
                return True

            if entry.password == hashed_password:
                return False

        return True

    @classmethod
    def retire_user(cls, user_id):
        """
        Updates the password in all rows corresponding to a user
        to an empty string as part of removing PII for user retirement.
        """
        return cls.objects.filter(user_id=user_id).update(password="")
