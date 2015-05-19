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

#from nti.contenttypes.courses.interfaces import ICourseInstance

from nti.dataserver import authorization as nauth

from nti.dataserver.interfaces import IUser

from nti.externalization.interfaces import LocatedExternalDict

from nti.ntiids.ntiids import find_object_with_ntiid

from nti.learning_network.interfaces import IAccessStatsSource
from nti.learning_network.interfaces import IProductionStatsSource

STATS_VIEW_NAME = "LearningNetworkStats"

def _get_stat_source( iface, user, course, timestamp ):
	if course and timestamp:
		stats_source = component.queryMultiAdapter( ( user, course, timestamp ), iface )
	elif course:
		stats_source = component.queryMultiAdapter( ( user, course ), iface )
	else:
		stats_source = iface( user )
	return stats_source

@view_config(	route_name='objects.generic.traversal',
				renderer='rest',
				request_method='GET',
				context=IUser,
				permission=nauth.ACT_NTI_ADMIN,
				name=STATS_VIEW_NAME )
class LearningNetworkStats( AbstractAuthenticatedView ):

	def __call__(self):
		# TODO Filter by user, course, timestamp, stat type
		user = self.context
		params = CaseInsensitiveDict( self.request.params )
		course_ntiid = params.get( 'Course' )
		timestamp = params.get( 'Timestamp' )
		timestamp = datetime.utcfromtimestamp( timestamp ) if timestamp else None

		course = None
		if course_ntiid:
			course = find_object_with_ntiid( course_ntiid )
			if course is None:
				return hexc.HTTPNotFound( "No course found for %s" % course_ntiid )

		access_source = _get_stat_source( IAccessStatsSource, user, course, timestamp )
		prod_source = _get_stat_source( IProductionStatsSource, user, course, timestamp )

		result = LocatedExternalDict()
		result['Access'] = access_source
		result['Production'] = prod_source
		return result
