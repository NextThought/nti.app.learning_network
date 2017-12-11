#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
.. $Id$
"""

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
 
import csv
import six
from io import BytesIO
from datetime import datetime
from datetime import timedelta
from collections import namedtuple
from collections import OrderedDict

from pyramid import httpexceptions as hexc

from pyramid.view import view_config

from requests.structures import CaseInsensitiveDict

from zope import component

from zope.cachedescriptors.property import Lazy

from nti.app.externalization.error import raise_json_error

from nti.app.learning_network.connections import get_connection_graphs

from nti.analytics.users import get_user_record

from nti.analytics.boards import get_topic_views
from nti.analytics.boards import get_forum_comments

from nti.analytics.resource_tags import get_note_views

from nti.analytics.stats.interfaces import IStats
from nti.analytics.stats.interfaces import IAnalyticsStatsSource

from nti.app.assessment.interfaces import IUsersCourseInquiry

from nti.app.base.abstract_views import AbstractAuthenticatedView

from nti.assessment.interfaces import IQModeledContentResponse
from nti.assessment.interfaces import IQNonGradableMultipleChoicePart

from nti.common.string import is_true

from nti.contentfragments.interfaces import IPlainTextContentFragment

from nti.contenttypes.courses.interfaces import ES_CREDIT

from nti.contenttypes.courses.interfaces import ICourseCatalog
from nti.contenttypes.courses.interfaces import ICourseInstance
from nti.contenttypes.courses.interfaces import ICourseEnrollments
from nti.contenttypes.courses.interfaces import ICourseCatalogEntry

from nti.dataserver import authorization as nauth

from nti.dataserver.authorization_acl import has_permission

from nti.dataserver.interfaces import IUser
from nti.dataserver.interfaces import IDataserverFolder
from nti.dataserver.interfaces import IEnumerableEntityContainer

from nti.dataserver.users.interfaces import IUserProfile

from nti.dataserver.users.users import User

from nti.externalization.interfaces import LocatedExternalDict
from nti.externalization.interfaces import StandardExternalFields

from nti.learning_network.interfaces import IAccessStatsSource
from nti.learning_network.interfaces import IOutcomeStatsSource
from nti.learning_network.interfaces import IProductionStatsSource
from nti.learning_network.interfaces import IInteractionStatsSource

from nti.mailer.interfaces import IEmailAddressable

from nti.ntiids.ntiids import find_object_with_ntiid

ITEM_COUNT = StandardExternalFields.ITEM_COUNT

STATS_VIEW_NAME = "LearningNetworkStats"
CONNECTIONS_VIEW_NAME = "LearningNetworkConnections"
SURVEY_STATS_VIEW_NAME = "SurveyLearningNetworkStats"

logger = __import__('logging').getLogger(__name__)


def _get_stat_source(iface, user, course, timestamp=None, max_timestamp=None):
    if course and timestamp and max_timestamp:
        stats_source = component.queryMultiAdapter((user, course, timestamp, max_timestamp), 
                                                   iface)
    elif course and timestamp:
        stats_source = component.queryMultiAdapter((user, course, timestamp),
                                                   iface)
    elif course:
        stats_source = component.queryMultiAdapter((user, course), iface)
    else:
        stats_source = iface(user, None)
    return stats_source


def _get_subscribers(user, course):
    return component.subscribers((user, course), IAnalyticsStatsSource)


def _get_stats_for_user(user, course, timestamp=None,
                        max_timestamp=None, exclude_outcome=False):
    access_source = _get_stat_source(IAccessStatsSource, user, course, 
                                     timestamp, max_timestamp)
    prod_source = _get_stat_source(IProductionStatsSource, user, course, 
                                   timestamp, max_timestamp)
    social_source = _get_stat_source(IInteractionStatsSource, user, course, 
                                     timestamp, max_timestamp)
    stats = _get_subscribers(user, course)
    stats.append(access_source)
    stats.append(prod_source)
    stats.append(social_source)
    if not exclude_outcome:
        outcome_source = _get_stat_source(IOutcomeStatsSource, user, course)
        stats.append(outcome_source)
    return stats


def _add_stats_to_user_dict(user_dict, user, course, timestamp):
    stats = _get_stats_for_user(user, course, timestamp)
    for stat in stats:
        user_dict[stat.display_name] = stat


class _AbstractCSVView(AbstractAuthenticatedView):

    def __init__(self, request):
        super(_AbstractCSVView, self).__init__(request)
        params = CaseInsensitiveDict(request.params)
        self.course_filter = params.get('filter', '')
        self.user_info = bool(params.get('UserInfo', False))
        self.opaque_id = bool(params.get('OpaqueUserId', True))
        self.instructors = bool(params.get('Instructors', False))
        self.exclude_user_parts = request.params.getall('ExcludeUserFilter')
        self.exclude_outcome_stats = bool(params.get('ExcludeOutcomeStats', False))
        # pylint: disable=no-member
        self._set_times(params)
        self._set_course_day_delta(params)

    def accept_course_entry(self, entry):
        # pylint: disable=no-member
        # Skip if no course, no match, or we have a course start param that
        # does not hit.
        logger.debug('Checking course (%s)', entry.ntiid)
        return self.course_filter \
           and self.course_filter in entry.ntiid \
           and (   self.course_start_time is None
                or self.course_start_time < entry.StartDate)


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=IDataserverFolder,
             permission=nauth.ACT_NTI_ADMIN,
             name=STATS_VIEW_NAME)
class LearningNetworkCSVStats(_AbstractCSVView):
    """
    Fetches and outputs stats in a CSV. Useful for generating data
    for research purposes. Can be given a filter for many courses.

    params:

            filter - str course filter on catalog entry ntiid

            UserInfo - whether to include username and other user info in results
                    (defaults to False)

            OpaqueUserId = whether to include an opaque user id in the results
                    (defaults to True)

            Instructors - whether to include instructor stats
                    (defaults to False)

            StartTime/EndTime - the timestamp boundaries on which to pull data (default None)

            CourseStartDayDelta - if no start/end time given, the number of days from course start
                    can be given to pull data from.

            CourseStartTime - part of the course filter that only pulls courses that start *before*
                    this date.

            ExcludeOutcomeStats - exclude outcome stats (defaults to False)

            ExcludeUserFilter - excludes usernames containing any parts of filter

    """

    type_stat_statvar_map = None

    def _get_source_str(self, source):
        return getattr(source, 'display_name', '')

    def _get_stat_str(self, source_type, stat_name, stat_var):
        result = '%s_%s_%s' % (source_type, stat_name, stat_var)
        return result

    def _get_type_stat_statvar_map(self, sources):
        """
        Build up a consistent map of type->stat->stat_field so
        our data points match up consistently for all users.
        We lazily create the header row using the first data sources we get.
        This allows us to dynamically generate headers based on stat fields.
        """
        if not self.type_stat_statvar_map:
            type_stat_statvar_map = {}
            for source in sources:
                source_type = self._get_source_str(source)
                type_stat_statvar_map[source_type] = stat_map = {}
                for source_var in dir(source):
                    if source_var.startswith('_'):
                        continue
                    stat = getattr(source, source_var)
                    if IStats.providedBy(stat):
                        stat_map[source_var] = source_stats = []
                        for stat_var in vars(stat):
                            # How do we get 'parameters'?
                            if not stat_var.startswith('_') and stat_var != 'parameters':
                                source_stats.append(stat_var)

            self.type_stat_statvar_map = type_stat_statvar_map
        return self.type_stat_statvar_map

    def _get_row_for_user(self, user, record, course, sources):
        """
        Gather the data dict for the user from the given sources.
        """
        type_stat_statvar_map = self._get_type_stat_statvar_map(sources)
        user_results = {}

        entry = ICourseCatalogEntry(course)
        user_results['course_title'] = entry.title
        user_results['course_ntiid'] = entry.ntiid
        user_record = get_user_record(user)
        if user_record is None:
            return
        # First user info
        if self.user_info:
            addr = IEmailAddressable(user, None)
            email = addr and addr.email
            user_results.update({'username': user.username,
                                 'username2': user_record.username2,
                                 'email': email})
            account_create_date = getattr(user, 'created', None)
            last_login = getattr(user, 'lastLoginTime', None)
            if last_login:
                last_login = datetime.utcfromtimestamp(last_login)
            user_results['last_login_time'] = last_login
            user_results['account_create_date'] = account_create_date
            user_results['enrollment_date'] = record.created if record else None
        if self.opaque_id:
            user_results['user_id'] = user_record.user_id

        # Then stat data
        for source in sources:
            source_type = self._get_source_str(source)
            stat_map = type_stat_statvar_map.get(self._get_source_str(source))
            for stat_name, stat_vars in stat_map.items():
                stat = getattr(source, stat_name)
                for stat_var in stat_vars:
                    stat_value = getattr(stat, stat_var) if stat is not None else ''
                    header_label = self._get_stat_str(source_type, stat_name, stat_var)
                    # Google sheets users a ' to signify we do not want
                    # auto-conversion by type.
                    stat_value = "'%s" % stat_value
                    user_results[header_label] = stat_value
        return user_results

    def _write_stats_for_user(self, writer, user, record, course, sources):
        user_results = self._get_row_for_user(user, record, course, sources)
        __traceback_info__ = user_results  # pylint: disable=unused-variable
        writer.writerow(user_results)

    def _set_course_day_delta(self, params):
        # pylint: disable=attribute-defined-outside-init
        self.day_delta_param = params.get('CourseStartDayDelta')
        self.day_delta = timedelta(days=int(self.day_delta_param)) if self.day_delta_param else None
        # Only courses started after this date.
        course_start_time = params.get('CourseStartTime')
        course_start_time = float(course_start_time) if course_start_time else None
        self.course_start_time = datetime.utcfromtimestamp(course_start_time) if course_start_time else None

    def _set_times(self, params):
        # pylint: disable=attribute-defined-outside-init
        start_time = params.get('StartTime')
        end_time = params.get('EndTime')
        self.start_time = datetime.utcfromtimestamp(start_time) if start_time else None
        self.end_time = datetime.utcfromtimestamp(end_time) if end_time else None

    def _get_headers(self, sources):
        """
        Write our headers:
                * course data
                * user data
                * additional headers
                * stats
        """
        header_labels = ['course_title', 'course_ntiid']
        if self.opaque_id:
            header_labels.append('user_id')
        if self.user_info:
            header_labels.extend(('username', 'username2', 'email',
                                  'enrollment_date', 'last_login_time',
                                  'account_create_date'))

        type_stat_statvar_map = self._get_type_stat_statvar_map(sources)

        for source in sources:
            source_headers = []
            source_type = self._get_source_str(source)
            stat_map = type_stat_statvar_map.get(source_type)
            for stat_name, stat_vars in stat_map.items():
                for stat_var in stat_vars:
                    header_label = self._get_stat_str(source_type, stat_name, stat_var)
                    source_headers.append(header_label)
            source_headers = sorted(source_headers)
            header_labels.extend(source_headers)
        return header_labels

    def _filter_user(self, user):
        """
        Filter any username containing items in our request param.
        """
        for user_exclude_part in self.exclude_user_parts or ():
            if user_exclude_part in user.username:
                logger.info('Filtering user (%s)', user.username)
                return True
        return False

    def __call__(self):
        course = self.context
        response = self.request.response
        response.content_encoding = str('identity')
        response.content_type = str('text/csv; charset=UTF-8')
        filename = '%s_stats.csv' % (self.course_filter.lower())
        response.content_disposition = str('attachment; filename="%s"' % filename)
        stream = BytesIO()
        writer = None

        catalog = component.getUtility(ICourseCatalog)

        for entry in catalog.iterCatalogEntries():
            course = ICourseInstance(entry, None)
            if course is None or not self.accept_course_entry(entry):
                continue

            logger.info('Fetching stat data for %s', entry.ntiid)

            # pylint: disable=too-many-function-args,not-an-iterable
            if self.instructors:
                user_records = ((x, None) for x in course.instructors)
            else:
                user_records = ((x.Principal, x)
                                for x in ICourseEnrollments(course).iter_enrollments())

            start_time = self.start_time
            end_time = self.end_time
            if      not start_time \
                and not end_time \
                and self.day_delta is not None:
                start_time = entry.StartDate - self.day_delta
                end_time = entry.StartDate + self.day_delta

            for user, record in user_records:
                if isinstance(user, six.string_types):
                    user = User.get_user(user)
                username = user.username
                user_profile = IUserProfile(user)
                email = getattr(user_profile, 'email', '') or ''
                if      user is not None \
                    and not username.endswith('@nextthought.com') \
                    and not email.endswith('@nextthought.com') \
                    and not self._filter_user(user):

                    sources = _get_stats_for_user(user,
                                                  course, start_time,
                                                  end_time,
                                                  self.exclude_outcome_stats)
                    if writer is None:
                        # We defer writing headers until we get our stat
                        # sources.
                        headers = self._get_headers(sources)
                        writer = csv.DictWriter(stream, headers)
                        writer.writeheader()
                    self._write_stats_for_user(
                        writer, user, record, course, sources)

        stream.flush()
        stream.seek(0)
        response.body_file = stream
        return response


_QuestionPartKeys = namedtuple("QuestionPartKeys", ("original_part_key", "part_keys"))


class DefaultSurveyHeaderProvider(object):
    """
    Provides question column headers.
    """

    def __init__(self, survey, survey_title):
        self.survey = survey
        self.survey_title = survey_title

    def _get_survey_question_part_keys(self, question, part, index, part_length):
        """
        Build our header name: '[survey] question [part] [supplemental]'.
        """
        content = IPlainTextContentFragment(question.content)
        result = '[%s] %s' % (self.survey_title, content)
        # Display part content or part index (if more than one part).
        part_content = None
        if part.content:
            part_content = IPlainTextContentFragment(part.content)
            part_content = part_content.strip()
        elif part_length > 1:
            part_content = str(index)
        if part_content:
            result = '%s [%s]' % (result, part_content)
        if result and isinstance(result, six.text_type):
            result = result.encode('utf-8')
        result = _QuestionPartKeys(result, None)
        return result

    def _get_headers_for_question(self, question):
        result = []
        part_length = len(question.parts or ())
        for idx, part in enumerate(question.parts or ()):
            question_keys = self._get_survey_question_part_keys(question, part,
                                                                idx, part_length)
            if question_keys.part_keys:
                result.extend(question_keys.part_keys)
            result.append(question_keys.original_part_key)
        return result

    def get_survey_headers(self):
        """
        Traverse the survey, building and storing reproducible keys (headers).
        """
        result = []
        for question in self.survey.questions or ():
            headers = self._get_headers_for_question(question)
            result.extend(headers)
        return result

    def _get_part_submission_results(self, question_keys, unused_part, response):
        result = {}
        if IQModeledContentResponse.providedBy(response):
            responses = response.value
        else:
            responses = (response,)
        # Append all of our result values in plain text.
        response_values = []
        for response_part in responses:
            try:
                response_part = IPlainTextContentFragment(response_part)
                response_part = response_part.strip()
            except TypeError:
                response_part = ''
            if response_part:
                response_values.append(response_part)
        response_display = ' - '.join(response_values) if response_values else ''
        if response_display and isinstance(response_display, six.text_type):
            response_display = response_display.encode('utf-8')
        result[question_keys.original_part_key] = response_display
        return result

    def get_results_for_submission(self, submission):
        """
        For the submission, return a dict of headers to responses matched to
        the appropriate survey-question-part key defined as the column header.
        """
        result = {}
        if submission is not None:
            # Now store our user's response for each question part.
            # Must make sure we map to keys stored in the writer.
            for question, sub_question in zip(self.survey.questions,
                                              submission.Submission.parts):
                assert question.ntiid == sub_question.inquiryId
                part_length = len(question.parts or ())
                for idx, part in enumerate(question.parts or ()):
                    question_keys = self._get_survey_question_part_keys(question,
                                                                        part,
                                                                        idx,
                                                                        part_length)
                    response = sub_question.parts[idx]
                    student_results = self._get_part_submission_results(question_keys,
                                                                        part,
                                                                        response)
                    result.update(student_results)
        return result


class ByAnswerSurveyHeaderProvider(DefaultSurveyHeaderProvider):
    """
    Provides csv column headers for each multiple choice option, with a
    binary (0/1) if the choice was chosen by the user. We also include
    the raw answer with the user response in a summary column.
    """

    def _get_choice_str(self, choice):
        choice = IPlainTextContentFragment(choice)
        choice = choice.strip()
        if choice and isinstance(choice, six.text_type):
            choice = choice.encode('utf-8')
        return choice

    def _get_survey_question_part_keys(self, question, part, *args):  # pylint: disable=arguments-differ
        """
        Build our header name: '[survey] question [part] [choice]'.
        """
        # pylint: disable=no-value-for-parameter
        keys = super(ByAnswerSurveyHeaderProvider, self)._get_survey_question_part_keys(question, part, *args)
        question_part_keys = keys
        result = question_part_keys
        if IQNonGradableMultipleChoicePart.providedBy(part):
            # Order matters here since we tag by index
            question_part_key = question_part_keys.original_part_key
            part_keys = []
            for choice in part.choices or ():
                choice = self._get_choice_str(choice)
                choice = '%s [%s]' % (question_part_key, choice)
                part_keys.append(choice)
            result = _QuestionPartKeys(question_part_key, part_keys)
        return result

    def _get_part_submission_results(self, question_keys, part, response):
        """
        Get a binary result for each multiple choice response.
        """
        if IQNonGradableMultipleChoicePart.providedBy(part):
            assert len(question_keys.part_keys or ()) == len(part.choices or ())
            result = {}
            if isinstance(response, six.integer_types):
                response = (response,)
            aggregate_response = []
            for response_idx in response or ():
                choice_response = self._get_choice_str(part.choices[response_idx])
                aggregate_response.append(choice_response)
            result[question_keys.original_part_key] = ', '.join(aggregate_response)
            for idx, question_key in enumerate(question_keys.part_keys or ()):
                result[question_key] = '1' if response and idx in response else '0'
        else:
            result = super(ByAnswerSurveyHeaderProvider, self)._get_part_submission_results(question_keys,
                                                                                            part,
                                                                                            response)
        return result


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=IDataserverFolder,
             permission=nauth.ACT_NTI_ADMIN,
             name=SURVEY_STATS_VIEW_NAME)
class LearningNetworkSurveyCSVStats(LearningNetworkCSVStats):
    """
    For the given course, fetch any registered analytic stats sources,
    supplemented by the post-survey data specified by the param.

    params:

            *params from super class*

            PostSurveyNTIID - [list] for each survey, fetch the survey
                    question/responses for each user.

            SurveyMultipleChoiceAnswerByColumn - report multiple choice responses
                    in the final survey as a choice per column, with a binary (0/1)
                    whether the user chose that response or not (default False).

    """

    def __init__(self, request):
        super(LearningNetworkSurveyCSVStats, self).__init__(request)
        params = CaseInsensitiveDict(request.params)

        self.survey_ids = self.request.params.getall('PostSurveyNTIID') \
                       or self.request.params.getall('surveyId')
        if not self.survey_ids:
            raise_json_error(self.request,
                             hexc.HTTPUnprocessableEntity,
                             {
                                 'message': u"Must supply survey_id.",
                             },
                             None)
        answer_by_column = params.get('SurveyMultipleChoiceAnswerByColumn', False)
        answer_by_column = is_true(answer_by_column)
        factory = ByAnswerSurveyHeaderProvider if answer_by_column else DefaultSurveyHeaderProvider
        # Useful to keep a consistent order.
        self.header_providers = OrderedDict()
        for survey in self.surveys:  # pylint: disable=not-an-iterable
            self.header_providers[survey.ntiid] = factory(survey, survey.title)

    @Lazy
    def surveys(self):
        results = []
        for survey_id in self.survey_ids:
            survey = find_object_with_ntiid(survey_id)
            if survey is None:
                raise hexc.HTTPUnprocessableEntity('Survey not found for %s' % survey_id)
            results.append(survey)
        return results

    def _get_headers(self, *args, **kwargs):  # pylint: disable=arguments-differ
        headers = super(LearningNetworkSurveyCSVStats, self)._get_headers(*args, **kwargs)
        for provider in self.header_providers.values():
            survey_headers = provider.get_survey_headers()
            headers.extend(survey_headers)
        return headers

    # pylint: disable=arguments-differ
    def _get_survey_submission(self, survey, user, course):
        course_inquiry = component.getMultiAdapter((course, user),
                                                   IUsersCourseInquiry)
        result = None
        try:
            result = course_inquiry[survey.ntiid]
        except KeyError:
            pass
        return result

    # pylint: disable=arguments-differ
    def _get_row_for_user(self, user, record, course, *args, **kwargs):
        """
        Gather the data dict for the user from the given sources.
        """
        user_results = super(LearningNetworkSurveyCSVStats, self)._get_row_for_user(user,
                                                                                    record,
                                                                                    course,
                                                                                    *args,
                                                                                    **kwargs)
        for survey in self.surveys:  # pylint: disable=not-an-iterable
            submission = self._get_survey_submission(survey, user, course)
            provider = self.header_providers[survey.ntiid]
            survey_results = provider.get_results_for_submission(submission)
            user_results.update(survey_results)
        return user_results


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=ICourseInstance,
             permission=nauth.ACT_NTI_ADMIN,
             name=STATS_VIEW_NAME)
class LearningNetworkCourseStats(AbstractAuthenticatedView):
    """
    For the given course (and possibly user or timestamp), return
    the learning network stats for each user enrolled in the course.
    """

    def __call__(self):
        # For beer-200, 3k students, 650s (5 students/s) with 55k loads.
        result = LocatedExternalDict()
        course = self.context
        params = CaseInsensitiveDict(self.request.params)
        username = params.get('Username')
        timestamp = params.get('Timestamp')
        timestamp = datetime.utcfromtimestamp(timestamp) if timestamp else None
        user = None
        usernames = ()
        if username:
            user = User.get_user(username)
            if user is None:
                raise_json_error(self.request,
                                 hexc.HTTPUnprocessableEntity,
                                 {
                                     'message': u"No user found %s." % username,
                                 },
                                 None)
            usernames = (username,)
        else:
            # pylint: disable=too-many-function-args
            enrollments = ICourseEnrollments(course)
            usernames = tuple(enrollments.iter_principals())

        for username in usernames:
            result[username] = user_dict = {}
            user = User.get_user(username)
            if user is not None:
                _add_stats_to_user_dict(user_dict, user, course, timestamp)
            else:
                logger.info('User (%s) in course not found.', username)
        result[ITEM_COUNT] = len(usernames)
        return result


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=IDataserverFolder,
             permission=nauth.ACT_NTI_ADMIN,
             name='SocialConnections')
class SocialConnectionsCSVStats(_AbstractCSVView):
    """
    Fetches and outputs stats in a CSV. Useful for generating data
    for research purposes.

    This just shows comment/notes views by for-credit students.
    Could add filters by type of viewing/commenting student as well
    as easily fetching the comment social connections (creator/reply-to).
    """

    def _get_scope_usernames(self, scope):
        result = set()
        if scope:
            # pylint: disable=too-many-function-args
            result = {
                x.lower() for x in IEnumerableEntityContainer(scope).iter_usernames()
            }
        return result

    def _get_instructors(self, course):
        instructor_usernames = {x.username.lower() for x in course.instructors}
        return instructor_usernames

    def _get_all_students(self, course):
        enrollments = ICourseEnrollments(course)
        # pylint: disable=too-many-function-args
        result = set(
            x.lower() for x in enrollments.iter_principals()
        )
        return result - self._get_instructors(course)

    @Lazy
    def _only_public_usernames(self):
        # PURCHASED falls in this category.
        # pylint: disable=no-member
        return self._all_students - self._for_credit_usernames

    def _get_for_credit_scope(self, course):
        return course.SharingScopes.get(ES_CREDIT)

    def _get_for_credit_usernames(self, course, all_students):
        scope = self._get_for_credit_scope(course)
        result = self._get_scope_usernames(scope)
        return result & all_students

    def _write_topic_views(self, writer, course, for_credit_usernames):
        """
        Write out those for credit students that have viewed
        comments to show social connections.
        """
        views = get_topic_views(course=course)
        comments_created = get_forum_comments(course=course)
        topic_comments = dict()
        for comment in comments_created:
            comments = topic_comments.setdefault(comment.topic_id, [])
            comments.append(comment)

        seen_comments = set()
        for view in views:
            comments = topic_comments.get(view.topic_id, ())
            if not comments:
                continue
            # Comment created before view.
            view_comments = (
                x for x in comments if x.timestamp < view.timestamp
            )
            for view_comment in view_comments:
                # User only sees comment the first time?
                key = (view.user_id, view_comment.comment_id)
                if key in seen_comments:
                    continue
                seen_comments.add(key)
                if view.user is None:
                    logger.warning('User is None: %s', view.user_id)
                    continue
                if view.user.username.lower() in for_credit_usernames:
                    writer.writerow((view.user_id,
                                     view_comment.user_id,
                                     view.timestamp,
                                     'CommentViewed'))

    def _write_note_views(self, writer, course, for_credit_usernames):
        """
        Write out those for credit students that have viewed
        notes to show social connections.
        """
        views = get_note_views(course=course)
        seen_notes = set()
        for view in views:
            if view.Note is None:
                continue
            notes = (view.Note,) + tuple(view.Note.referents)
            for note in notes:
                # Viewed if created and readable (which could have changed)...
                # pylint: disable=protected-access
                key = (view.user_id, note._ds_intid)
                if key in seen_notes:
                    continue
                seen_notes.add(key)
                if view.user is None:
                    logger.warning('User is None: %s', view.user_id)
                    continue
                if      note.created < view.timestamp \
                    and view.user.username.lower() in for_credit_usernames \
                    and has_permission(nauth.ACT_READ, note, view.user.username):
                    user_record = get_user_record(note.creator)
                    writer.writerow((view.user_id,
                                     user_record.user_id,
                                     view.timestamp,
                                     'NoteViewed'))

    def __call__(self):
        # pylint: disable=no-member
        self._initialize()
        course = self.context
        response = self.request.response
        response.content_encoding = str('identity')
        response.content_type = str('text/csv; charset=UTF-8')
        filename = '%s_social_stats.csv' % (self.course_filter.lower())
        response.content_disposition = str('attachment; filename="%s"' % filename)
        stream = BytesIO()
        writer = csv.writer(stream)

        catalog = component.getUtility(ICourseCatalog)
        for entry in catalog.iterCatalogEntries():
            # pylint: disable=attribute-defined-outside-init
            course = self.course = ICourseInstance(entry, None)
            if course is None or not self.accept_course_entry(entry):
                continue
            writer.writerow(('source', 'target', 'timestamp', 'label'))
            all_students = self._get_all_students(course)
            for_credit_usernames = self._get_for_credit_usernames(course, all_students)
            self._write_topic_views(writer, course, for_credit_usernames)
            self._write_note_views(writer, course, for_credit_usernames)

        stream.flush()
        stream.seek(0)
        response.body_file = stream
        return response


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=IUser,
             permission=nauth.ACT_NTI_ADMIN,
             name=STATS_VIEW_NAME)
class LearningNetworkUserStats(AbstractAuthenticatedView):
    """
    For the given user (and possibly course or timestamp), return
    the learning network stats.
    """

    def __call__(self):
        user = self.context
        params = CaseInsensitiveDict(self.request.params)
        course_ntiid = params.get('Course')
        timestamp = params.get('Timestamp')
        timestamp = datetime.utcfromtimestamp(timestamp) if timestamp else None
        course = None
        if course_ntiid:
            course = find_object_with_ntiid(course_ntiid)
            course = ICourseInstance(course, None)
            if course is None:
                raise_json_error(self.request,
                                 hexc.HTTPUnprocessableEntity,
                                 {
                                     'message': u"No course found for %s" % course_ntiid,
                                 },
                                 None)
        result = LocatedExternalDict()
        _add_stats_to_user_dict(result, user, course, timestamp)
        return result


@view_config(route_name='objects.generic.traversal',
             renderer='rest',
             request_method='GET',
             context=ICourseInstance,
             permission=nauth.ACT_NTI_ADMIN,
             name=CONNECTIONS_VIEW_NAME)
class CourseConnectionGraph(AbstractAuthenticatedView):
    """
    For the given course (and possibly timestamp), return the connections
    (in graph or gif form?).
    """

    def __call__(self):
        course = self.context
        params = CaseInsensitiveDict(self.request.params)
        timestamp = params.get('Timestamp')
        timestamp = datetime.utcfromtimestamp(timestamp) if timestamp else None
        try:
            get_connection_graphs(course, timestamp)
        except TypeError:
            raise_json_error(self.request,
                             hexc.HTTPServerError,
                             {
                                 'message': u"Cannot create connection graphs; pygraphviz missing?",
                             },
                             None)
        # What do we want to return, gif?. TODO:
        return hexc.HTTPNoContent()
