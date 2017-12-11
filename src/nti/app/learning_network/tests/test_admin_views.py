#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

# pylint: disable=protected-access,too-many-public-methods

from hamcrest import not_none
from hamcrest import has_entry
from hamcrest import assert_that

from nti.app.learning_network.admin_views import STATS_VIEW_NAME

from nti.app.testing.application_webtest import ApplicationLayerTest

from nti.app.testing.decorators import WithSharedApplicationMockDS

from nti.dataserver.users.users import User

from nti.dataserver.tests import mock_dataserver


class TestAppLearningNetwork(ApplicationLayerTest):

    @WithSharedApplicationMockDS(testapp=True, users=True)
    def test_stats_view(self):
        """
        Get learning stats for a user.
        """
        with mock_dataserver.mock_db_trans(self.ds, site_name='platform.ou.edu'):
            user = User.create_user(username=u'new_user1', dataserver=self.ds,
                                    external_value={'realname': u'Jim Bob', 'email': u'foo@bar.com'})

            url = '/dataserver2/users/%s/%s' % (user.username, STATS_VIEW_NAME)
        result = self.testapp.get(url)
        body = result.json_body
        assert_that(body, has_entry('Access', not_none()))
        assert_that(body, has_entry('Production', not_none()))
        assert_that(body, has_entry('Interaction', not_none()))
