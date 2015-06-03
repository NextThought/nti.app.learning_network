#!/usr/bin/env python
# -*- coding: utf-8 -*
"""
$Id$
"""
from __future__ import print_function, unicode_literals, absolute_import, division

__docformat__ = "restructuredtext en"

logger = __import__('logging').getLogger(__name__)

import os

from pygraphviz import AGraph

from calendar import timegm as _calendar_timegm

from nti.learning_network.interfaces import IConnectionsSource

def _get_boundary( timestamp ):
	beginning = timestamp.replace( hour=0, minute=0, second=0, microsecond=0 )
	return beginning

def _do_accum( graph_dict ):
	"""
	Accumulate all previous connections into current.
	Our last bucket should contain all edges.
	"""
	accum = {}
	for timestamp in sorted( graph_dict.keys() ):
		vals = graph_dict[ timestamp ]
		for username, targets in vals.items():
			# Losing weight
			accum.setdefault( username, {} ).update( targets )
		graph_dict[ timestamp ] = dict( accum )

def _build_timestamp_nodes_edges_dict( connections ):
	# Bucket into dailies
	results = {}
	for connection in connections:
		timestamp = _get_boundary( connection.Timestamp )
		node_dict = results.setdefault( timestamp, {} )
		target_dict = node_dict.setdefault( connection.Source, {} )
		target_dict[ connection.Target ] = None # Label
	_do_accum( results )
	return results

def _do_store( timestamp, graph ):
	env_dir = os.getenv('DATASERVER_DIR' )
	path = os.path.join( env_dir, 'data/learning_network' )
	if not os.path.exists( path ):
		os.mkdir( path )
	path = os.path.join( path, 'connections' )
	if not os.path.exists( path ):
		os.mkdir( path )
	# FIXME Should create a site/context directory
	timestamp = _calendar_timegm( timestamp.timetuple() )
	filepath = os.path.join( path, '%s.png' % timestamp )
	graph.draw( filepath, prog='neato' )

def _get_graphs( connections ):
	graphs = []
	timestamp_dict = _build_timestamp_nodes_edges_dict( connections )
	for timestamp, nodes_edges in timestamp_dict.items():
		graph = AGraph( nodes_edges )
		_do_store( timestamp, graph )
		graphs.append( graph )
	return graphs

def get_connection_graphs( course, timestamp=None ):
	connection_source = IConnectionsSource( course )
	connections = connection_source.get_connections( timestamp )
	graphs = _get_graphs( connections )
	return graphs
