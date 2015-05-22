#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function, unicode_literals, absolute_import
__docformat__ = "restructuredtext en"

# disable: accessing protected members, too many methods
# pylint: disable=W0212,R0904

from hamcrest import not_none
from hamcrest import has_entry
from hamcrest import assert_that

from nti.dataserver.users import User

import nti.dataserver.tests.mock_dataserver as mock_dataserver

from nti.app.testing.decorators import WithSharedApplicationMockDS
from nti.app.testing.application_webtest import ApplicationLayerTest

from ..admin_views import STATS_VIEW_NAME

class TestAppLearningNetwork( ApplicationLayerTest ):

	@WithSharedApplicationMockDS(testapp=True, users=True)
	def test_stats_view(self):
		"""
		Get learning stats for a user.
		"""
		with mock_dataserver.mock_db_trans(self.ds, site_name='platform.ou.edu'):
			user = User.create_user( 	username='new_user1', dataserver=self.ds,
										external_value={'realname':'Jim Bob', 'email': 'foo@bar.com'} )

			url = '/dataserver2/users/%s/%s' % (user.username, STATS_VIEW_NAME)
		result = self.testapp.get( url )
		body = result.json_body
		assert_that( body, has_entry( 'Access', not_none() ))
		assert_that( body, has_entry( 'Production', not_none() ))
		assert_that( body, has_entry( 'Interaction', not_none() ))

