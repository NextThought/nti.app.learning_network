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

from collections import Mapping

from datetime import datetime
from datetime import timedelta

from zope import component

from pyramid.view import view_config
from pyramid import httpexceptions as hexc

from nti.analytics.users import get_user_record
from nti.analytics.stats.interfaces import IStats, IAnalyticsStatsSource

from nti.app.base.abstract_views import AbstractAuthenticatedView

from nti.common.maps import CaseInsensitiveDict

from nti.contenttypes.courses.interfaces import ICourseCatalog
from nti.contenttypes.courses.interfaces import ICourseInstance
from nti.contenttypes.courses.interfaces import ICourseEnrollments

from nti.dataserver import authorization as nauth

from nti.dataserver.interfaces import IUser
from nti.dataserver.interfaces import IDataserverFolder

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

@view_config(route_name='objects.generic.traversal',
			 renderer='rest',
			 request_method='GET',
			 context=IDataserverFolder,
			 permission=nauth.ACT_NTI_ADMIN,
			 name=STATS_VIEW_NAME)
class LearningNetworkCSVStats(AbstractAuthenticatedView):
	"""
	Fetches and outputs stats in a CSV. Useful for generating data
	for research purposes.
	"""

	type_stat_statvar_map = None

	def _get_source_str(self, source):
		return getattr( source, 'display_name', '' )

	def _get_headers( self, writer, *sources ):
		"""
		Build up a consistent map of type->stat->stat_field so
		our data points match up consistently for all users.
		"""
		# TODO: Look at csv.DictWriter, much easier.
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
							# How do we get 'parameters'?
							if 		not stat_var.startswith( '_' ) \
								and stat_var != 'parameters':
								source_stats.append( stat_var )

			self.type_stat_statvar_map = type_stat_statvar_map

			# Now build our headers (match iteration with stat iteration)
			header_labels = []
			if self.user_info:
				header_labels.extend( ('user_id', 'username', 'username2') )
			elif self.opaque_id:
				header_labels.append( 'user_id' )

			for source in sources:
				source_type = self._get_source_str( source )
				stat_map = type_stat_statvar_map.get( source_type  )
				for stat_name, stat_vars in stat_map.items():
					for stat_var in stat_vars:
						header_labels.append( '%s_%s_%s' % ( source_type, stat_name, stat_var ))
			writer.writerow( header_labels )
		return self.type_stat_statvar_map

	def _write_stats_for_user( self, writer, user, course, start_time, end_time ):
		sources = _get_stats_for_user( user, course, start_time, end_time )
		type_stat_statvar_map = self._get_headers( writer, *sources )
		user_results = []
		if self.user_info:
			user_record = get_user_record( user )
			user_results.extend( (user_record.user_id, user.username, user_record.username2) )
		elif self.opaque_id:
			user_record = get_user_record( user )
			user_results.append( user_record.user_id )
		for source in sources:
			stat_map = type_stat_statvar_map.get( self._get_source_str( source )  )
			for stat_name, stat_vars in stat_map.items():
				stat = getattr( source, stat_name )
				for stat_var in stat_vars:
					stat_value = getattr( stat, stat_var ) if stat is not None else ''
					user_results.append( stat_value )

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

	def _initialize(self):
		params = CaseInsensitiveDict(self.request.params)
		self.course_filter = params.get( 'filter', '' )
		self.user_info = bool( params.get( 'UserInfo', False ) )
		self.opaque_id = bool( params.get( 'OpaqueUserId', True ))
		self.instructors = bool( params.get( 'Instructors', False ))
		self._set_times( params )
		self._set_course_day_delta( params )

	def __call__(self):
		self._initialize()
		course = self.context
		response = self.request.response
		response.content_encoding = str( 'identity' )
		response.content_type = str('text/csv; charset=UTF-8')
		filename = '%s_learning_network_stats.csv' % ( self.course_filter.lower() )
		response.content_disposition = str( 'attachment; filename="%s"' % filename )
		stream = BytesIO()
		writer = csv.writer(stream)

		catalog = component.getUtility( ICourseCatalog )

		for entry in catalog.iterCatalogEntries():
			course = ICourseInstance( entry, None )
			# Skip if no course, no match, not finished, or we have a
			# course start param that does not hit.
			logger.info( 'Checking course (%s)', entry.ntiid )
			if 		course is None \
				or 	self.course_filter not in entry.ntiid \
				or (self.course_start_time and self.course_start_time < entry.StartDate):
				continue

			writer.writerow( (entry.ProviderUniqueID, entry.StartDate, entry.EndDate, entry.ntiid) )

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
					self._write_stats_for_user( writer, user, course, start_time, end_time )

		stream.flush()
		stream.seek(0)
		response.body_file = stream
		return response

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
	FIXME: Needs to inherit from base class.
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
	For the given course (and possibly timestamp), return
	the connections (in graph or gif form?).
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
