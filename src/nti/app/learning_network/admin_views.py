#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
.. $Id$
"""

from __future__ import print_function, unicode_literals, absolute_import, division
__docformat__ = "restructuredtext en"

logger = __import__('logging').getLogger(__name__)

from datetime import datetime

from zope import component

from pyramid.view import view_config
from pyramid import httpexceptions as hexc

from nti.app.base.abstract_views import AbstractAuthenticatedView

from nti.common.maps import CaseInsensitiveDict

from nti.contenttypes.courses.interfaces import ICourseInstance
from nti.contenttypes.courses.interfaces import ICourseEnrollments

from nti.dataserver.interfaces import IUser
from nti.dataserver.users.users import User
from nti.dataserver import authorization as nauth

from nti.externalization.interfaces import LocatedExternalDict

from nti.learning_network.interfaces import IAccessStatsSource
from nti.learning_network.interfaces import IProductionStatsSource
from nti.learning_network.interfaces import IInteractionStatsSource

from nti.ntiids.ntiids import find_object_with_ntiid

from .connections import get_connection_graphs

STATS_VIEW_NAME = "LearningNetworkStats"
CONNECTIONS_VIEW_NAME = "LearningNetworkConnections"

def _get_stat_source(iface, user, course, timestamp):
	if course and timestamp:
		stats_source = component.queryMultiAdapter((user, course, timestamp), iface)
	elif course:
		stats_source = component.queryMultiAdapter((user, course), iface)
	else:
		stats_source = iface(user, None)
	return stats_source

def _add_stats_to_user_dict(user_dict, user, course, timestamp):
	access_source = _get_stat_source(IAccessStatsSource, user, course, timestamp)
	prod_source = _get_stat_source(IProductionStatsSource, user, course, timestamp)
	social_source = _get_stat_source(IInteractionStatsSource, user, course, timestamp)
	user_dict['Access'] = access_source
	user_dict['Production'] = prod_source
	user_dict['Interaction'] = social_source

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
