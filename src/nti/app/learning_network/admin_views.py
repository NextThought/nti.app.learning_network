#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
.. $Id$
"""

from __future__ import print_function, unicode_literals, absolute_import, division
__docformat__ = "restructuredtext en"

logger = __import__('logging').getLogger(__name__)

import csv

from io import BytesIO

from datetime import datetime
from datetime import timedelta

from zope import component

from pyramid.view import view_config
from pyramid import httpexceptions as hexc

from nti.analytics.users import get_user_record

from nti.analytics.boards import get_topic_views
from nti.analytics.boards import get_forum_comments

from nti.analytics.resource_tags import get_note_views

from nti.analytics.stats.interfaces import IStats
from nti.analytics.stats.interfaces import IAnalyticsStatsSource

from nti.app.analytics_registration.view_mixins import RegistrationIDViewMixin
from nti.app.analytics_registration.view_mixins import RegistrationSurveyCSVMixin

from nti.app.assessment.interfaces import IUsersCourseInquiry

from nti.app.base.abstract_views import AbstractAuthenticatedView

from nti.common.maps import CaseInsensitiveDict

from nti.common.property import Lazy

from nti.contentfragments.interfaces import IPlainTextContentFragment

from nti.contenttypes.courses.interfaces import ES_CREDIT

from nti.contenttypes.courses.interfaces import ICourseCatalog
from nti.contenttypes.courses.interfaces import ICourseInstance
from nti.contenttypes.courses.interfaces import ICourseEnrollments

from nti.dataserver import authorization as nauth

from nti.dataserver.authorization_acl import has_permission

from nti.dataserver.interfaces import IUser
from nti.dataserver.interfaces import IDataserverFolder
from nti.dataserver.interfaces import IEnumerableEntityContainer

from nti.dataserver.users.users import User

from nti.externalization.interfaces import LocatedExternalDict

from nti.learning_network.interfaces import IAccessStatsSource
from nti.learning_network.interfaces import IOutcomeStatsSource
from nti.learning_network.interfaces import IProductionStatsSource
from nti.learning_network.interfaces import IInteractionStatsSource

from nti.ntiids.ntiids import find_object_with_ntiid

from nti.app.learning_network.connections import get_connection_graphs

STATS_VIEW_NAME = "LearningNetworkStats"
CONNECTIONS_VIEW_NAME = "LearningNetworkConnections"
SURVEY_STATS_VIEW_NAME = "SurveyLearningNetworkStats"

def _get_stat_source(iface, user, course, timestamp=None, max_timestamp=None):
	if course and timestamp and max_timestamp:
		stats_source = component.queryMultiAdapter((user, course, timestamp, max_timestamp), iface)
	elif course and timestamp:
		stats_source = component.queryMultiAdapter((user, course, timestamp), iface)
	elif course:
		stats_source = component.queryMultiAdapter((user, course), iface)
	else:
		stats_source = iface(user, None)
	return stats_source

def _get_subscribers( user, course ):
	return component.subscribers( (user, course), IAnalyticsStatsSource )

def _get_stats_for_user( user, course, timestamp=None, max_timestamp=None ):
	access_source = _get_stat_source(IAccessStatsSource, user, course, timestamp, max_timestamp)
	prod_source = _get_stat_source(IProductionStatsSource, user, course, timestamp, max_timestamp)
	social_source = _get_stat_source(IInteractionStatsSource, user, course, timestamp, max_timestamp)
	outcome_source = _get_stat_source(IOutcomeStatsSource, user, course)
	stats = _get_subscribers(user, course)
	stats.append( access_source )
	stats.append( prod_source )
	stats.append( social_source )
	stats.append( outcome_source )
	return stats

def _add_stats_to_user_dict(user_dict, user, course, timestamp):
	stats = _get_stats_for_user( user, course, timestamp )
	for stat in stats:
		user_dict[stat.display_name] = stat

class _AbstractCSVView(AbstractAuthenticatedView):

	def _initialize(self):
		params = CaseInsensitiveDict(self.request.params)
		self.course_filter = params.get( 'filter', '' )
		self.user_info = bool( params.get( 'UserInfo', False ) )
		self.opaque_id = bool( params.get( 'OpaqueUserId', True ))
		self.instructors = bool( params.get( 'Instructors', False ))
		self._set_times( params )
		self._set_course_day_delta( params )

	def accept_course_entry(self, entry):
		# Skip if no course, no match, or we have a course start param that does not hit.
		logger.debug( 'Checking course (%s)', entry.ntiid )
		return 	self.course_filter \
			and self.course_filter in entry.ntiid \
			and (	self.course_start_time is None \
				or 	self.course_start_time < entry.StartDate)

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

		UserInfo - whether to includer username and other user info in results
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

	"""

	type_stat_statvar_map = None

	def _get_source_str(self, source):
		return getattr( source, 'display_name', '' )

	def _get_stat_str(self, source_type, stat_name, stat_var ):
		return '%s_%s_%s' % ( source_type, stat_name, stat_var )

	def _get_type_stat_statvar_map( self, sources ):
		"""
		Build up a consistent map of type->stat->stat_field so
		our data points match up consistently for all users.
		We lazily create the header row using the first data sources we get.
		This allows us to dynamically generate headers based on stat fields.
		"""
		if not self.type_stat_statvar_map:
			type_stat_statvar_map = {}
			for source in sources:
				source_type = self._get_source_str( source )
				type_stat_statvar_map[ source_type ] = stat_map = {}
				for source_var in dir( source ):
					if source_var.startswith( '_' ):
						continue
					stat = getattr( source, source_var )
					if IStats.providedBy( stat ):
						stat_map[ source_var ] = source_stats = []

						for stat_var in vars( stat ):
							# XXX: How do we get 'parameters'?
							if 		not stat_var.startswith( '_' ) \
								and stat_var != 'parameters':
								source_stats.append( stat_var )

			self.type_stat_statvar_map = type_stat_statvar_map

		return self.type_stat_statvar_map

	def _get_row_for_user( self, user, course, sources ):
		"""
		Gather the data dict for the user from the given sources.
		"""
		type_stat_statvar_map = self._get_type_stat_statvar_map( sources )
		user_results = {}
		user_record = get_user_record( user )
		if user_record is None:
			return
		# First user info
		if self.user_info:
			user_results.update( {'user_id': user_record.user_id,
								  'username': user.username,
								  'username2': user_record.username2} )
		elif self.opaque_id:
			user_results['user_id'] = user_record.user_id

		# Then stat data
		for source in sources:
			source_type = self._get_source_str( source )
			stat_map = type_stat_statvar_map.get( self._get_source_str( source )  )
			for stat_name, stat_vars in stat_map.items():
				stat = getattr( source, stat_name )
				for stat_var in stat_vars:
					stat_value = getattr( stat, stat_var ) if stat is not None else ''
					header_label = self._get_stat_str( source_type, stat_name, stat_var )
					user_results[header_label] = stat_value
		return user_results

	def _write_stats_for_user( self, writer, user, course, sources ):
		user_results = self._get_row_for_user( user, course, sources )
		writer.writerow( user_results )

	def _set_course_day_delta(self, params):
		self.day_delta_param = params.get( 'CourseStartDayDelta' )
		self.day_delta = timedelta( days=int( self.day_delta_param ) ) \
						if self.day_delta_param else None
		# Only courses started after this date.
		course_start_time = params.get( 'CourseStartTime' )
		course_start_time = float( course_start_time ) if course_start_time else None
		self.course_start_time = datetime.utcfromtimestamp( course_start_time ) \
								if course_start_time else None

	def _set_times(self, params):
		start_time = params.get( 'StartTime' )
		end_time = params.get( 'EndTime' )
		self.start_time = datetime.utcfromtimestamp( start_time ) if start_time else None
		self.end_time = datetime.utcfromtimestamp( end_time ) if end_time else None

	def _get_headers(self, sources):
		"""
		Write our headers:
			* user
			* additional headers
			* stats
		"""
		# Now build our headers (match iteration with stat iteration)
		header_labels = []
		if self.user_info:
			header_labels.extend( ('user_id', 'username', 'username2') )
		elif self.opaque_id:
			header_labels.append( 'user_id' )

		type_stat_statvar_map = self._get_type_stat_statvar_map( sources )

		for source in sources:
			source_type = self._get_source_str( source )
			stat_map = type_stat_statvar_map.get( source_type  )
			for stat_name, stat_vars in stat_map.items():
				for stat_var in stat_vars:
					header_label = self._get_stat_str( source_type, stat_name, stat_var )
					header_labels.append( header_label )
		return header_labels

	def __call__(self):
		self._initialize()
		course = self.context
		response = self.request.response
		response.content_encoding = str( 'identity' )
		response.content_type = str('text/csv; charset=UTF-8')
		filename = '%s_stats.csv' % ( self.course_filter.lower() )
		response.content_disposition = str( 'attachment; filename="%s"' % filename )
		stream = BytesIO()
		writer = None

		catalog = component.getUtility( ICourseCatalog )

		for entry in catalog.iterCatalogEntries():
			course = ICourseInstance( entry, None )
			if course is None or not self.accept_course_entry( entry ):
				continue

			logger.info( 'Fetching stat data for %s', entry.ntiid )

			if self.instructors:
				usernames = tuple( course.instructors )
			else:
				usernames = tuple(ICourseEnrollments(course).iter_principals())

			start_time = self.start_time
			end_time = self.end_time
			if 		not start_time \
				and not end_time \
				and self.day_delta is not None:
				start_time = entry.StartDate - self.day_delta
				end_time = entry.StartDate + self.day_delta

			for username in usernames:
				user = User.get_user(username)
				if 		user is not None \
					and not username.endswith( '@nextthought.com' ):

					sources = _get_stats_for_user( user, course, start_time, end_time )
					if writer is None:
						# We defer writing headers until we get our stat sources.
						headers = self._get_headers( sources )
						writer = csv.DictWriter( stream, headers )
						writer.writeheader()
					self._write_stats_for_user( writer, user, course, sources )

		stream.flush()
		stream.seek(0)
		response.body_file = stream
		return response

@view_config(route_name='objects.generic.traversal',
			 renderer='rest',
			 request_method='GET',
			 context=IDataserverFolder,
			 permission=nauth.ACT_NTI_ADMIN,
			 name=SURVEY_STATS_VIEW_NAME)
class LearningNetworkSurveyCSVStats(LearningNetworkCSVStats,
									RegistrationSurveyCSVMixin,
									RegistrationIDViewMixin):
	"""
	For the given course, fetch any registered analytic stats sources,
	supplemented by the post-survey data specified by the param.

	params:

		*params from super class*

		PostSurveyNTIID - fetch the survey question/responses for each user.

	"""

	@Lazy
	def survey_id(self):
		params = CaseInsensitiveDict( self.request.params )
		survey_id = params.get( 'PostSurveyNTIID' ) or params.get( 'surveyId' )
		if not survey_id:
			raise hexc.HTTPUnprocessibleEntity( 'Must supply survey_id.' )
		return survey_id

	@Lazy
	def survey(self):
		survey = find_object_with_ntiid( self.survey_id )
		if survey is None:
			raise hexc.HTTPUnprocessibleEntity( 'Survey not found for %s', self.survey_id )
		return survey

	@Lazy
	def survey_title(self):
		return self.survey.title

	def _get_survey_question_part_key(self, question, part=None, index=None):
		content = IPlainTextContentFragment( question.content )
		result = '[%s] %s' % ( self.survey_title, content )
		if part is not None:
			if part.content:
				part_content = IPlainTextContentFragment( part.content )
			else:
				part_content = str( index )
			result = '%s - %s' % (result, part_content)
		if result and isinstance( result, unicode ):
			result = result.encode( 'utf-8' )
		return result

	def _add_survey_headers(self, header_list):
		"""
		Traverse the survey, building and storing reproducible keys (headers).
		"""
		for question in self.survey.questions or ():
			if len( question.parts or () ) > 1:
				for idx, part in enumerate( question.parts ):
					question_key = self._get_survey_question_part_key( question,
																	   part,
																	   idx )
					header_list.append( question_key )
			else:
				question_key = self._get_survey_question_part_key( question )
				header_list.append( question_key )
		return header_list

	def _get_headers(self, *args, **kwargs):
		headers = super( LearningNetworkSurveyCSVStats, self )._get_headers( *args,
																			 **kwargs )
		headers = self._add_survey_headers( headers )
		return headers

	def _get_survey_submission(self, user, course):
		course_inquiry = component.getMultiAdapter((course, user),
												   IUsersCourseInquiry)
		result = None
		try:
			result = course_inquiry[self.survey.ntiid]
		except KeyError:
			pass
		return result

	def _get_survey_responses(self, user, course):
		"""
		For the user and course, pull the user's survey submission
		(if available), return responses matched to the appropriate
		survey-question-part key.
		"""
		submission = self._get_survey_submission( user, course )
		result = {}
		if submission is not None:
			# Now store our user's response for each question part.
			# Must make sure we map to keys stored in the writer.
			for question, sub_question in zip( self.survey.questions,
											   submission.Submission.parts):
				assert question.ntiid == sub_question.inquiryId
				if len( question.parts or () ) > 1:
					assert len( question.parts ) == len( sub_question.parts )
					for idx, part in enumerate( question.parts ):
						question_key = self._get_survey_question_part_key( question,
																	   	   part,
																	   	   idx )
						result[question_key] = sub_question.parts[idx]
				else:
					question_key = self._get_survey_question_part_key( question )
					result[question_key] = sub_question.parts[0]
		return result

	def _get_row_for_user( self, user, course, *args, **kwargs ):
		"""
		Gather the data dict for the user from the given sources.
		"""
		user_results = super( LearningNetworkSurveyCSVStats, self )._get_row_for_user( user,
																					   course,
																					   *args,
																					   **kwargs )
		survey_results = self._get_survey_responses( user, course )
		user_results.update( survey_results )
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
				return hexc.HTTPNotFound("No user found %s" % username)

			usernames = (username,)
		else:
			usernames = tuple(ICourseEnrollments(course).iter_principals())

		for username in usernames:
			result[username] = user_dict = {}
			user = User.get_user(username)
			if user is not None:
				_add_stats_to_user_dict(user_dict, user, course, timestamp)
			else:
				logger.info('User (%s) in course not found.', username)

		result['ItemCount'] = len(usernames)
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
			result = {x.lower() for x in IEnumerableEntityContainer(scope).iter_usernames()}
		return result

	def _get_instructors(self, course):
		instructor_usernames = {x.username.lower() for x in course.instructors}
		return instructor_usernames

	def _get_all_students(self, course):
		enrollments = ICourseEnrollments( course )
		result = set((x.lower() for x in enrollments.iter_principals()))
		return result - self._get_instructors( course )

	@Lazy
	def _only_public_usernames(self):
		# PURCHASED falls in this category.
		return self._all_students - self._for_credit_usernames

	def _get_for_credit_scope(self, course):
		return course.SharingScopes.get(ES_CREDIT)

	def _get_for_credit_usernames(self, course, all_students):
		scope = self._get_for_credit_scope( course )
		result = self._get_scope_usernames( scope )
		return result & all_students

	def _write_topic_views( self, writer, course, for_credit_usernames ):
		"""
		Write out those for credit students that have viewed
		comments to show social connections.
		"""
		views = get_topic_views( course=course )
		comments_created = get_forum_comments( course=course )
		topic_comments = dict()
		for comment in comments_created:
			comments = topic_comments.setdefault( comment.topic_id, [] )
			comments.append( comment )

		seen_comments = set()
		for view in views:
			comments = topic_comments.get( view.topic_id, () )
			if not comments:
				continue
			# Comment created before view.
			view_comments = (x for x in comments if x.timestamp < view.timestamp)
			for view_comment in view_comments:
				# User only sees comment the first time?
				key = (view.user_id, view_comment.comment_id)
				if key in seen_comments:
					continue
				seen_comments.add( key )
				if view.user is None:
					logger.warn( 'User is None: %s', view.user_id)
					continue
				if 		view.user.username.lower() in for_credit_usernames:
					writer.writerow( (view.user_id,
									  view_comment.user_id,
									  view.timestamp,
									  'CommentViewed') )

	def _write_note_views( self, writer, course, for_credit_usernames ):
		"""
		Write out those for credit students that have viewed
		notes to show social connections.
		"""
		views = get_note_views( course=course )
		seen_notes = set()
		for view in views:
			if view.Note is None:
				continue
			notes = (view.Note,) + tuple( view.Note.referents )
			for note in notes:
				# Viewed if created and readable (which could have changed)...
				key = (view.user_id, note._ds_intid)
				if key in seen_notes:
					continue
				seen_notes.add( key )
				if view.user is None:
					logger.warn( 'User is None: %s', view.user_id)
					continue
				if 		note.created < view.timestamp \
					and view.user.username.lower() in for_credit_usernames \
					and has_permission( nauth.ACT_READ, note, view.user.username ):
					user_record = get_user_record( note.creator )
					writer.writerow( (view.user_id,
									  user_record.user_id,
									  view.timestamp,
									  'NoteViewed') )

	def __call__(self):
		self._initialize()
		course = self.context
		response = self.request.response
		response.content_encoding = str( 'identity' )
		response.content_type = str('text/csv; charset=UTF-8')
		filename = '%s_social_stats.csv' % ( self.course_filter.lower() )
		response.content_disposition = str( 'attachment; filename="%s"' % filename )
		stream = BytesIO()
		writer = csv.writer(stream)

		catalog = component.getUtility( ICourseCatalog )

		for entry in catalog.iterCatalogEntries():
			course = self.course = ICourseInstance( entry, None )
			if course is None or not self.accept_course_entry( entry ):
				continue

			writer.writerow( ('source', 'target', 'timestamp', 'label') )

			all_students = self._get_all_students( course )
			for_credit_usernames = self._get_for_credit_usernames( course, all_students )
			self._write_topic_views( writer, course, for_credit_usernames )
			self._write_note_views( writer, course, for_credit_usernames )

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
				return hexc.HTTPNotFound("No course found for %s" % course_ntiid)

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
			raise hexc.HTTPServerError("Cannot create connection graphs; pygraphviz missing?")
		# TODO What do we want to return, gif?
		return hexc.HTTPNoContent()
