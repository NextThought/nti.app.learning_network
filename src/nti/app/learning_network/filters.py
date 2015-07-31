#!/usr/bin/env python
# -*- coding: utf-8 -*
"""
.. $Id$
"""

from __future__ import print_function, unicode_literals, absolute_import, division
__docformat__ = "restructuredtext en"

logger = __import__('logging').getLogger(__name__)

from zope import interface

from nti.appserver.interfaces import ICreatableObjectFilter

@interface.implementer(ICreatableObjectFilter)
class _LearningNetworkContentObjectFilter(object):

	PREFIX = 'application/vnd.nextthought.learningnetwork'

	def __init__(self, context=None):
		pass

	def filter_creatable_objects(self, terms):
		for name in list(terms):  # mutating
			if name.startswith(self.PREFIX):
				terms.pop(name, None)
		return terms
