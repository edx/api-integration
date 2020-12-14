""" API implementation for group-oriented interactions. """
import json
import uuid

from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from django.utils.translation import ugettext_lazy as _
from edx_solutions_api_integration.courseware_access import get_course
from edx_solutions_api_integration.models import APIUser as User
from edx_solutions_api_integration.models import (CourseGroupRelationship,
                                                  GroupProfile,
                                                  GroupRelationship)
from edx_solutions_api_integration.permissions import (SecureAPIView,
                                                       SecureListAPIView)
from edx_solutions_api_integration.utils import generate_base_uri, str2bool
from edx_solutions_organizations import serializers
from edx_solutions_projects.serializers import (BasicWorkgroupSerializer,
                                                GroupSerializer)
from rest_framework import status
from rest_framework.response import Response

RELATIONSHIP_TYPES = {'hierarchical': 'h', 'graph': 'g'}


class GroupsList(SecureListAPIView):
    """
    ### The GroupsList view allows clients to retrieve/append a list of Group entities
    - URI: ```/api/groups/```
    - GET: Returns a JSON representation (array) of the set of Group entities
        * type: __required__, Set filtering parameter
    - POST: Provides the ability to append to the Group entity set
        * name: The name of the group being added
        * type: __required__, Client-specified Group entity type, used for set filtering
        * data: Free-form, JSON-formatted metadata attached to this Group entity
    - POST Example:

            {
                "name" : "Alpha Series",
                "type" : "series",
                "data" : {
                    "display_name" : "Demo Program",
                    "start_date" : "2014-01-01",
                    "end_date" : "2014-12-31"
                }
            }
    ### Use Cases/Notes:
    * GET requests for _all_ groups are not currently allowed via the API
    * If no 'type' parameter is specified during GET, the server will return a 400 Bad Request
    * 'type' is a free-form field used to tag/filter group entities.
    * Some sample of types include:
        ** workgroup: a group of users working on a project together
        ** series: a group of related courses
        ** company: a group of groups (such as departments)
    * 'data' is a free-form field containing type-specific metadata in JSON format, which bypasses the need for
        extensive database modeling
    * Some sample 'data' elements include:
        ** series: display_name, start_date, end_date
        ** organization: display_name, contact_name, phone, email
    * Ultimately, both 'type' and 'data' are determined by the client/caller.  Open edX has no type or data
        specifications at the present time.
    """
    serializer_class = GroupSerializer

    def post(self, request):
        """
        POST /api/groups
        """
        group_type = request.data.get('type', None)
        if group_type is None:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        response_data = {}
        # Group name must be unique, but we need to support dupes
        group = Group.objects.create(name=str(uuid.uuid4()))
        original_group_name = request.data.get('name', None)
        if original_group_name is None or len(original_group_name) == 0:
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
        group.name = '{:04d}: {}'.format(group.id, original_group_name)
        group.record_active = True
        group.save()

        # Create a corresponding relationship management record
        GroupRelationship.objects.create(group_id=group.id, parent_group=None)

        # Create a corresponding profile record (for extra meta info)
        data = request.data.get('data', {})
        profile, _ = GroupProfile.objects.get_or_create(
            group_id=group.id,
            group_type=group_type,
            name=original_group_name,
            data=json.dumps(data)
        )

        response_data['id'] = group.id
        response_data['name'] = profile.name
        response_data['uri'] = '{}/{}'.format(generate_base_uri(request, True), group.id)
        response_status = status.HTTP_201_CREATED
        return Response(response_data, status=response_status)

    def get(self, request, *args, **kwargs):
        """
        if checks if get request has `type` filter
        """
        group_type = request.query_params.get('type', None)
        if group_type is None:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return self.list(request, *args, **kwargs)

    def get_queryset(self):
        """
        returns queryset filter by group type
        """
        group_type = self.request.query_params.get('type', None)
        groups = Group.objects.select_related("groupprofile").filter(groupprofile__group_type=group_type)
        return groups


class GroupsDetail(SecureAPIView):
    """
    ### The GroupsDetail view allows clients to interact with a specific Group entity
    - URI: ```/api/groups/{group_id}```
    - GET: Returns a JSON representation of the specified Group entity
    - POST: Provides the ability to modify specific fields for this Group entity
        * type: Client-specified Group entity type
        * data: Free-form, JSON-formatted metadata attached to this Group entity
    - POST Example:

            {
                "type" : "series",
                "data" : {
                    "display_name" : "Demo Program",
                    "start_date" : "2014-01-01",
                    "end_date" : "2014-12-31"
                }
            }
    ### Use Cases/Notes:
    * Use the GroupsDetail view to obtain the current state for a specific Group
    * For POSTs, you may include only those parameters you wish to modify, for example:
        ** Modifying the start_date for a 'series' without changing the 'type' field
    * A GET response will additionally include a list of URIs to available sub-resources:
        ** Related Users (/api/groups/{group_id}/users)
        ** Related Courses (/api/groups/{group_id}/courses)
        ** Related Groups(/api/groups/{group_id}/groups)
    """

    def post(self, request, group_id):
        """
        POST /api/groups/{group_id}
        """
        response_data = {}
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        profile, _ = GroupProfile.objects.get_or_create(group_id=group_id)
        group_name = request.data.get('name', None)
        if group_name:
            formatted_name = '{:04d}: {}'.format(existing_group.id, group_name)
            existing_group.name = formatted_name
            profile.name = group_name
        group_type = request.data.get('type', None)
        if group_type:
            profile.group_type = group_type
        data = request.data.get('data', None)
        if data:
            profile.data = json.dumps(data)
        existing_group.save()
        profile.save()
        response_data['id'] = existing_group.id
        response_data['name'] = profile.name
        response_data['type'] = profile.group_type
        response_data['uri'] = generate_base_uri(request)
        return Response(response_data, status=status.HTTP_200_OK)

    def get(self, request, group_id):
        """
        GET /api/groups/{group_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        response_data['id'] = existing_group.id
        response_data['uri'] = base_uri
        response_data['resources'] = []
        resource_uri = '{}/users'.format(base_uri)
        response_data['resources'].append({'uri': resource_uri})
        resource_uri = '{}/groups'.format(base_uri)
        response_data['resources'].append({'uri': resource_uri})
        resource_uri = '{}/courses'.format(base_uri)
        response_data['resources'].append({'uri': resource_uri})
        try:
            group_profile = GroupProfile.objects.get(group_id=group_id)
        except ObjectDoesNotExist:
            group_profile = None
        if group_profile:
            if group_profile.name:
                response_data['name'] = group_profile.name
            else:
                response_data['name'] = existing_group.name
            if group_profile.group_type:
                response_data['type'] = group_profile.group_type
            data = group_profile.data
            if data:
                response_data['data'] = json.loads(data)
        else:
            response_data['name'] = existing_group.name
        return Response(response_data, status=status.HTTP_200_OK)

    def delete(self, request, group_id):  # pylint: disable=W0612,W0613
        """
        DELETE removes an existing group from the system
        """
        try:
            Group.objects.get(id=group_id).delete()
        except ObjectDoesNotExist:
            pass
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class GroupsUsersList(SecureAPIView):
    """
    ### The GroupsUserList view allows clients to interact with the set of User entities related to the specified Group
    - URI: ```/api/groups/{group_id}/users/```
    - GET: Returns a JSON representation (array) of the set of related User entities
    - POST: Append a User or a set of User entities to the specified group
        * user_id: __required__, The identifier for the User/Users being added
    - POST Example:

            {
                "user_id" : 1,2,3
            }
    ### Use Cases/Notes:
    * Use the GroupsUsersList view to manage User membership for a specific Group
    * For example, as a newly-added member of a 'workgroup' group, a User could be presented with a list of their peers
    * Once a User Group exists, you can additionally link to Courses and other Groups (see GroupsCoursesList,
        GroupsGroupsList)
    """

    def post(self, request, group_id):
        """
        POST /api/groups/{group_id}/users/
        """
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)

        user_id = request.data.get('user_id', None)
        if user_id:
            user_id = list(map(int, str(user_id).split(',')))
        else:
            return Response({'message': _('user_id is missing')}, status=status.HTTP_400_BAD_REQUEST)

        existing_users = User.objects.filter(id__in=user_id).distinct()
        if existing_users:
            existing_group.user_set.add(*existing_users)
        else:
            return Response({}, status.HTTP_404_NOT_FOUND)

        return Response({}, status=status.HTTP_201_CREATED)

    def get(self, request, group_id):
        """
        GET /api/groups/{group_id}/users/
        """
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        users = existing_group.user_set.all()

        is_active = request.query_params.get('is_active', None)
        if is_active:
            users = users.filter(is_active=str2bool(is_active))

        response_data = {}
        response_data['users'] = []
        for user in users:
            user_data = {}
            user_data['id'] = user.id
            user_data['email'] = user.email
            user_data['username'] = user.username
            user_data['first_name'] = user.first_name
            user_data['last_name'] = user.last_name
            response_data['users'].append(user_data)
        response_status = status.HTTP_200_OK
        return Response(response_data, status=response_status)


class GroupsUsersDetail(SecureAPIView):
    """
    ### The GroupsUsersDetail view allows clients to interact with a specific Group-User relationship
    - URI: ```/api/groups/{group_id}/users/{user_id}```
    - GET: Returns a JSON representation of the specified Group-User relationship
    - DELETE: Removes an existing Group-User relationship
    ### Use Cases/Notes:
    * Use the GroupsUsersDetail to validate that a User is a member of a specific Group
    * Cancelling a User's membership in a Group is as simple as calling DELETE on the URI
    """

    def get(self, request, group_id, user_id):
        """
        GET /api/groups/{group_id}/users/{user_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        try:
            existing_group = Group.objects.get(id=group_id)
            existing_relationship = existing_group.user_set.get(id=user_id)
        except ObjectDoesNotExist:
            existing_group = None
            existing_relationship = None
        if existing_group and existing_relationship:
            response_data['group_id'] = existing_group.id
            response_data['user_id'] = existing_relationship.id
            response_data['uri'] = base_uri
            response_status = status.HTTP_200_OK
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)

    def delete(self, request, group_id, user_id):  # pylint: disable=W0612,W0613
        """
        DELETE removes/inactivates/etc. an existing group-user relationship
        """
        try:
            existing_group = Group.objects.get(id=group_id)
            existing_group.user_set.remove(user_id)
            existing_group.save()
        except ObjectDoesNotExist:
            pass
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class GroupsGroupsList(SecureAPIView):
    """
    ### The GroupsGroupsList view allows clients to interact with the set of Groups related to the specified Group
    - URI: ```/api/groups/{group_id}/groups/```
    - GET: Returns a JSON representation (array) of the set of related Group entities
    - POST: Provides the ability to append to the related Group entity set
        * group_id: __required__, The name of the Group being added
        * relationship_type: __required__, Relationship paradigm, select from the following values:
            ** 'g', _graph_, create a graph(aka, linked) relationship with the specified Group
            ** 'h', _hierarchical_, create a parent-child relationship with the specified Group
    - POST Example:

            {
                "group_id" : 1234,
                "relationship_type" : "g"
            }
    ### Use Cases/Notes:
    * Use a graph-type relationship when you simply want to indicate a link between two groups:
        ** Linking a course series with a particular company
        ** Linking a user workgroup with a particular course series
    * Use a hierarchical-type relationship when you want to enforce a parent-child link between two groups:
        ** Linking a company (parent) to a department (child)
        ** Linking a user workgroup (parent) to a breakout user workgroup (child)
    * Note that posting a new hierarchical relationship for a child group having a parent will overwrite the current
        relationship:
        ** POST /groups/123/groups { "group_id": 246}
        ** GET /groups/123/groups/246 -> 200 OK
        ** POST /groups/987/groups {"group_id": 246}
        ** GET /groups/123/groups/246 -> 404 NOT FOUND
        ** GET /groups/987/groups/246 -> 200 OK
    * Once a Group Group exists, you can additionally link to Users and Courses (see GroupsUsersList, GroupsCoursesList)
    """

    def post(self, request, group_id):
        """
        POST /api/groups/{group_id}/groups/{related_group_id}
        """
        response_data = {}
        to_group_id = request.data.get('group_id', None)
        if not to_group_id:
            return Response({'message': _('group_id is missing')}, status=status.HTTP_400_BAD_REQUEST)

        relationship_type = request.data.get('relationship_type', None)
        if not relationship_type:
            return Response({'message': _('relationship_type is missing')}, status=status.HTTP_400_BAD_REQUEST)

        base_uri = generate_base_uri(request)
        response_data['uri'] = '{}/{}'.format(base_uri, to_group_id)
        response_data['group_id'] = str(to_group_id)
        response_data['relationship_type'] = relationship_type
        try:
            from_group_relationship = GroupRelationship.objects.get(group__id=group_id)
            to_group_relationship = GroupRelationship.objects.get(group__id=to_group_id)
        except ObjectDoesNotExist:
            from_group_relationship = None
            to_group_relationship = None
        if from_group_relationship and to_group_relationship:
            response_status = status.HTTP_201_CREATED
            if relationship_type == RELATIONSHIP_TYPES['hierarchical']:
                to_group_relationship.parent_group = from_group_relationship
                to_group_relationship.save()
            elif relationship_type == RELATIONSHIP_TYPES['graph']:
                from_group_relationship.add_linked_group_relationship(to_group_relationship)
            else:
                response_data['message'] = "Relationship type '%s' not currently supported" % relationship_type
                response_data['field_conflict'] = 'relationship_type'
                response_status = status.HTTP_406_NOT_ACCEPTABLE
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)

    def get(self, request, group_id):
        """
        GET /api/groups/{group_id}/groups/{related_group_id}
        """
        try:
            from_group_relationship = GroupRelationship.objects.get(group__id=group_id)
        except ObjectDoesNotExist:
            from_group_relationship = None
        response_data = []
        if from_group_relationship:
            base_uri = generate_base_uri(request)
            group_type = request.query_params.get('type', None)
            child_groups = GroupRelationship.objects.filter(parent_group_id=group_id)
            linked_groups = from_group_relationship.get_linked_group_relationships()
            if group_type:
                profiles = GroupProfile.objects.filter(group_type=group_type).values_list('group_id', flat=True)
                if profiles:
                    child_groups = child_groups.filter(group_id__in=profiles)
                    linked_groups = linked_groups.filter(to_group_relationship__in=profiles)
            if child_groups:
                for group in child_groups:
                    response_data.append({
                        "id": group.group_id,
                        "relationship_type": RELATIONSHIP_TYPES['hierarchical'],
                        "uri": '{}/{}'.format(base_uri, group.group.id)
                    })
            if linked_groups:
                for group in linked_groups:
                    response_data.append({
                        "id": group.to_group_relationship_id,
                        "relationship_type": RELATIONSHIP_TYPES['graph'],
                        "uri": '{}/{}'.format(base_uri, group.to_group_relationship_id)
                    })
            response_status = status.HTTP_200_OK
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)


class GroupsGroupsDetail(SecureAPIView):
    """
    ### The GroupsGroupsDetail view allows clients to interact with a specific Group-Group relationship
    - URI: ```/api/groups/{group_id}/groups/{related_group_id}```
    - GET: Returns a JSON representation of the specified Group-Group relationship
    - DELETE: Removes an existing Group-Group relationship
    ### Use Cases/Notes:
    * Use the GroupsGroupsDetail operation to confirm that a relationship exists between two Groups
        ** Is the current workgroup linked to the specified company?
        ** Is the current course series linked to the specified workgroup?
    * To remove an existing Group-Group relationship, simply call DELETE on the URI
    """

    def get(self, request, group_id, related_group_id):
        """
        GET /api/groups/{group_id}/groups/{related_group_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        response_data['from_group_id'] = group_id
        response_data['to_group_id'] = related_group_id
        response_status = status.HTTP_404_NOT_FOUND

        try:
            from_group_relationship = GroupRelationship.objects.get(group__id=group_id)
            to_group_relationship = GroupRelationship.objects.get(group__id=related_group_id)
        except ObjectDoesNotExist:
            return Response(response_data, response_status)

        if to_group_relationship and str(to_group_relationship.parent_group_id) == str(group_id):
            response_data['relationship_type'] = RELATIONSHIP_TYPES['hierarchical']
            response_status = status.HTTP_200_OK
        else:
            linked_group_exists = from_group_relationship.check_linked_group_relationship(
                to_group_relationship,
                symmetrical=True
            )
            if linked_group_exists:
                response_data['relationship_type'] = RELATIONSHIP_TYPES['graph']
                response_status = status.HTTP_200_OK
        return Response(response_data, response_status)

    def delete(self, request, group_id, related_group_id):  # pylint: disable=W0613
        """
        DELETE /api/groups/{group_id}/groups/{related_group_id}
        """
        try:
            from_group_relationship = GroupRelationship.objects.get(group__id=group_id)
        except ObjectDoesNotExist:
            from_group_relationship = None
        try:
            to_group_relationship = GroupRelationship.objects.get(group__id=related_group_id)
        except ObjectDoesNotExist:
            to_group_relationship = None
        if from_group_relationship:
            if to_group_relationship:
                if to_group_relationship.parent_group_id is from_group_relationship.group_id:
                    to_group_relationship.parent_group_id = None
                    to_group_relationship.save()
                else:
                    from_group_relationship.remove_linked_group_relationship(to_group_relationship)
                    from_group_relationship.save()
            # No 'else' clause here -> It's ok if we didn't find a match
            response_status = status.HTTP_204_NO_CONTENT
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response({}, status=response_status)


class GroupsCoursesList(SecureAPIView):
    """
    ### The GroupsCoursesList view allows clients to interact with the set of Courses related to the specified Group
    - URI: ```/api/groups/{group_id}/courses/```
    - GET: Returns a JSON representation (array) of the set of related Course entities
    - POST: Provides the ability to append to the related Course entity set
        * course_id: __required__, The name of the Course being added
    - POST Example:

            {
                "course_id" : "edx/demo/course",
            }
    ### Use Cases/Notes:
    * Create a Group of Courses to model cases such as an academic program or topical series
    * Once a Course Group exists, you can additionally link to Users and other Groups (see GroupsUsersList,
        GroupsGroupsList)
    """

    def post(self, request, group_id):
        """
        POST /api/groups/{group_id}/courses/
        """
        response_data = {}
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)

        course_id = request.data.get('course_id', None)
        if not course_id:
            return Response({'message': _('course_id is missing')}, status=status.HTTP_400_BAD_REQUEST)

        base_uri = generate_base_uri(request)
        response_data['uri'] = '{}/{}'.format(base_uri, course_id)

        existing_course, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612,C0301
        if not existing_course:
            return Response({}, status.HTTP_404_NOT_FOUND)

        try:
            existing_relationship = CourseGroupRelationship.objects.get(course_id=course_id, group=existing_group)
        except ObjectDoesNotExist:
            existing_relationship = None

        if existing_relationship is None:
            new_relationship = CourseGroupRelationship.objects.create(course_id=course_id, group=existing_group)
            response_data['group_id'] = str(new_relationship.group_id)
            response_data['course_id'] = str(new_relationship.course_id)
            response_status = status.HTTP_201_CREATED
        else:
            response_data['message'] = "Relationship already exists."
            response_status = status.HTTP_409_CONFLICT
        return Response(response_data, status=response_status)

    def get(self, request, group_id):
        """
        GET /api/groups/{group_id}/courses/
        """
        response_data = {}
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        members = CourseGroupRelationship.objects.filter(group=existing_group)
        response_data = []
        for member in members:
            course, course_key, course_content = get_course(request, request.user, member.course_id)  # pylint: disable=W0612,C0301
            course_data = {
                'course_id': member.course_id,
                'display_name': course.display_name
            }
            response_data.append(course_data)
        response_status = status.HTTP_200_OK
        return Response(response_data, status=response_status)


class GroupsCoursesDetail(SecureAPIView):
    """
    ### The GroupsCoursesDetail view allows clients to interact with a specific Group-Course relationship
    - URI: ```/api/groups/{group_id}/courses/{course_id}```
    - GET: Returns a JSON representation of the specified Group-Course relationship
    - DELETE: Removes an existing Group-Course relationship
    ### Use Cases/Notes:
    * Use the GroupsCoursesDetail to validate that a Course is linked to a specific Group
        * Is Course part of the specified series?
        * Is Course linked to the specified workgroup?
    * Removing a Course from a Group is as simple as calling DELETE on the URI
        * Remove a course from the specified academic program
    """

    def get(self, request, group_id, course_id):
        """
        GET /api/groups/{group_id}/courses/{course_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        try:
            existing_group = Group.objects.get(id=group_id)
            existing_relationship = CourseGroupRelationship.objects.get(course_id=course_id, group=existing_group)
        except ObjectDoesNotExist:
            existing_group = None
            existing_relationship = None
        if existing_group and existing_relationship:
            response_data['group_id'] = existing_group.id
            response_data['course_id'] = existing_relationship.course_id
            response_status = status.HTTP_200_OK
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)

    def delete(self, request, group_id, course_id):  # pylint: disable=W0613
        """
        DELETE /api/groups/{group_id}/courses/{course_id}
        """
        try:
            existing_group = Group.objects.get(id=group_id)
            existing_group.coursegrouprelationship_set.get(course_id=course_id).delete()
            existing_group.save()
        except ObjectDoesNotExist:
            pass
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class GroupsOrganizationsList(SecureAPIView):
    """
    ### The GroupsOrganizationsList view allows clients to interact with the set of Organizations related to the
        specified Group
    - URI: ```/api/groups/{group_id}/organizations/```
    - GET: Returns a JSON representation (array) of the set of related Organization entities

    ### Use Cases/Notes:
    * View all of the Organizations related to a particular Program (currently modeled as a Group entity)
    """

    def get(self, request, group_id):  # pylint: disable=W0613
        """
        GET /api/groups/{group_id}/organizations/
        """
        response_data = {}
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        response_data = []
        for org in existing_group.organizations.all():
            serializer = serializers.OrganizationSerializer(org, context={'request': request})
            response_data.append(serializer.data)  # pylint: disable=E1101
        return Response(response_data, status=status.HTTP_200_OK)


class GroupsWorkgroupsList(SecureListAPIView):
    """
    ### The GroupsWorkgroupsList view allows clients to retrieve a list of workgroups a group has
    - URI: ```/api/groups/{group_id}/workgroups/```
    - GET: Provides paginated list of workgroups for a group
    To filter the group's workgroup set by course
    GET ```/api/groups/{group_id}/workgroups/?course_id={course_id}```
    """

    serializer_class = BasicWorkgroupSerializer

    def get_queryset(self):
        group_id = self.kwargs['group_id']
        course_id = self.request.query_params.get('course_id', None)
        try:
            group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            raise Http404

        queryset = group.workgroups.all()

        if course_id:
            queryset = queryset.filter(project__course_id=course_id)
        return queryset
