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

from nti.app.base.abstract_views import AbstractAuthenticatedView

from nti.common.maps import CaseInsensitiveDict

from nti.contenttypes.courses.interfaces import ICourseCatalog
from nti.contenttypes.courses.interfaces import ICourseInstance
from nti.contenttypes.courses.interfaces import ICourseEnrollments

from nti.dataserver.interfaces import IUser, IDataserverFolder
from nti.dataserver.users.users import User
from nti.dataserver import authorization as nauth

from nti.externalization.interfaces import LocatedExternalDict

from nti.learning_network.interfaces import IStats
from nti.learning_network.interfaces import IAccessStatsSource
from nti.learning_network.interfaces import IOutcomeStatsSource
from nti.learning_network.interfaces import IProductionStatsSource
from nti.learning_network.interfaces import IInteractionStatsSource

from nti.ntiids.ntiids import find_object_with_ntiid

from .connections import get_connection_graphs

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

def _get_stats_for_user( user, course, timestamp=None, max_timestamp=None ):
	access_source = _get_stat_source(IAccessStatsSource, user, course, timestamp, max_timestamp)
	prod_source = _get_stat_source(IProductionStatsSource, user, course, timestamp, max_timestamp)
	social_source = _get_stat_source(IInteractionStatsSource, user, course, timestamp, max_timestamp)
	outcome_source = _get_stat_source(IOutcomeStatsSource, user, course)
	return access_source, prod_source, social_source, outcome_source

def _add_stats_to_user_dict(user_dict, user, course, timestamp):
	access_source, prod_source, social_source, outcome_source = _get_stats_for_user( user, course, timestamp )
	user_dict['Access'] = access_source
	user_dict['Production'] = prod_source
	user_dict['Interaction'] = social_source
	user_dict['Outcomes'] = outcome_source

@view_config(route_name='objects.generic.traversal',
			 renderer='rest',
			 request_method='GET',
			 context=IDataserverFolder,
			 permission=nauth.ACT_NTI_ADMIN,
			 name=STATS_VIEW_NAME)
class LearningNetworkCSVStats(AbstractAuthenticatedView):

	type_stat_statvar_map = None

	def _get_source_str(self, source):
		if IAccessStatsSource.providedBy( source ):
			return 'Access'
		if IProductionStatsSource.providedBy( source ):
			return 'Production'
		if IInteractionStatsSource.providedBy( source ):
			return 'Interaction'
		if IOutcomeStatsSource.providedBy( source ):
			return 'Outcome'

	def _get_headers( self, writer, *sources ):
		"""
		Build up a consistent map of type->stat->stat_field so
		our data points match up consistently for all users.
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
							# How do we get 'parameters'?
							if 		not stat_var.startswith( '_' ) \
								and stat_var != 'parameters':
								source_stats.append( stat_var )

			self.type_stat_statvar_map = type_stat_statvar_map

			# Now build our headers (match iteration with stat iteration)
			header_labels = []
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
		for source in sources:
			stat_map = type_stat_statvar_map.get( self._get_source_str( source )  )
			for stat_name, stat_vars in stat_map.items():
				stat = getattr( source, stat_name )
				for stat_var in stat_vars:
					stat_value = getattr( stat, stat_var )
					user_results.append( stat_value )

		writer.writerow( user_results )

	def __call__(self):
		course = self.context
		params = CaseInsensitiveDict(self.request.params)
		course_filter = params.get( 'filter', '' )
		day_delta = params.get( 'CourseStartDayDelta' )
		day_delta = timedelta( days=int( day_delta ) ) if day_delta else None

		response = self.request.response
		response.content_encoding = str( 'identity' )
		response.content_type = str('text/csv; charset=UTF-8')
		filename = '%s_learning_network_stats.csv' % course_filter.lower()
		response.content_disposition = str( 'attachment; filename="%s"' % filename )
		stream = BytesIO()
		writer = csv.writer(stream)

		catalog = component.getUtility( ICourseCatalog )
		now = datetime.utcnow()

		for entry in catalog.iterCatalogEntries():
			course = ICourseInstance( entry, None )
			# Skip if no course, no match, or not finished.
			if 		course is None \
				or 	course_filter not in entry.ProviderUniqueID \
				or  entry.EndDate > now:
				continue
			usernames = tuple(ICourseEnrollments(course).iter_principals())

			start_time = end_time = None
			if day_delta is not None:
				start_time = entry.StartDate - day_delta
				end_time = entry.StartDate + day_delta

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
