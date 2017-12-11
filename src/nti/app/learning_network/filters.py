#!/usr/bin/env python
# -*- coding: utf-8 -*
"""
.. $Id$
"""

from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

from zope import interface

from nti.dataserver.interfaces import ICreatableObjectFilter

logger = __import__('logging').getLogger(__name__)


@interface.implementer(ICreatableObjectFilter)
class _LearningNetworkContentObjectFilter(object):

    PREFIX_1 = u'application/vnd.nextthought.learningnetwork'
    PREFIX_2 = u'application/vnd.nextthought.learning_network'

    def __init__(self, context=None):
        pass

    def filter_creatable_objects(self, terms):
        for name in tuple(terms):  # mutating
            if name.startswith(self.PREFIX_1) or name.startswith(self.PREFIX_2):
                terms.pop(name, None)
        return terms
