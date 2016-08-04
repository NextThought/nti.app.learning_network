#!/usr/bin/env python
# -*- coding: utf-8 -*
"""
.. $Id$
"""

from __future__ import print_function, unicode_literals, absolute_import, division
__docformat__ = "restructuredtext en"

logger = __import__('logging').getLogger(__name__)

import os
from calendar import timegm as _calendar_timegm

try:
	from pygraphviz import AGraph
except ImportError:  # PyPy?
	AGraph = None

from nti.contenttypes.courses.interfaces import ICourseCatalogEntry

from nti.learning_network.interfaces import IConnectionsSource

from nti.site.site import getSite

def _get_boundary(timestamp):
	beginning = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
	return beginning

def _do_accum(graph_dict):
	"""
	Accumulate all previous connections into current bucket.
	Our last bucket should contain all edges.
	"""
	accum = {}
	for timestamp in sorted(graph_dict.keys()):
		vals = graph_dict[ timestamp ]
		for username, targets in vals.items():
			# Losing weight
			accum.setdefault(username, {}).update(targets)
		graph_dict[ timestamp ] = dict(accum)

def _build_timestamp_nodes_edges_dict(connections):
	# Bucket into dailies
	results = {}
	for connection in connections:
		timestamp = _get_boundary(connection.Timestamp)
		node_dict = results.setdefault(timestamp, {})
		target_dict = node_dict.setdefault(connection.Source, {})
		target_dict[ connection.Target ] = None  # Label
	_do_accum(results)
	return results

def _initialize_dirs(context):
	"""
	Initialize our dirs, returning the full path.
	"""
	site = getSite()
	site_name = site.__name__
	context = ICourseCatalogEntry(context)
	context_name = context.ntiid
	ext_path = 'data/learning_network/connections/%s/%s' % (site_name, context_name)

	path = os.getenv('DATASERVER_DIR')
	for path_part in ext_path.split('/'):
		path = os.path.join(path, path_part)
		if not os.path.exists(path):
			os.mkdir(path)
	return path

def _do_store(timestamp, graph, course):
	# Store our graph persistently
	path = _initialize_dirs(course)
	timestamp = _calendar_timegm(timestamp.timetuple())
	file_path = os.path.join(path, '%s.png' % timestamp)
	# Once a file exists, we assume it will never be updated.
	if not os.path.exists(file_path):
		graph.layout()
		graph.draw(file_path, prog='neato')

def _format_graph(graph):
	graph.edge_attr['color'] = '#494949'

	graph.node_attr['shape'] = 'circle'
	graph.node_attr['fixedsize'] = 'true'
	graph.node_attr['label'] = ' '  # space is important
	graph.node_attr['fontcolor'] = '#494949'
	graph.node_attr['fontsize'] = '10'
	graph.node_attr['width'] = '.2'
	graph.node_attr['height'] = '.2'
	graph.node_attr['fillcolor'] = '#757474'

	graph.graph_attr['label'] = 'Connections'
	graph.graph_attr['fontcolor'] = '#494949'
	graph.graph_attr['fontsize'] = '10'
	graph.graph_attr['size' ] = '7.75,10.25'

def _get_graphs(connections, course):
	if AGraph is None:
		raise TypeError("pygraphviz is not avaiable")

	graphs = []
	timestamp_dict = _build_timestamp_nodes_edges_dict(connections)
	for timestamp, nodes_edges in timestamp_dict.items():
		graph = AGraph(nodes_edges)
		_format_graph(graph)
		_do_store(timestamp, graph, course)
		graphs.append(graph)
	return graphs

def get_connection_graphs(course, timestamp=None):
	connection_source = IConnectionsSource(course)
	connections = connection_source.get_connections(timestamp)
	graphs = _get_graphs(connections, course)
	return graphs
