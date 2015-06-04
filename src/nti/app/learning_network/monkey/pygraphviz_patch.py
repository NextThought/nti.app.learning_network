import gevent

"""
JAM - 06.02.2015
pygraphviz-1.2 (python2.7)
Note: This should be unnecessary under python3.

`pygraphviz` needs file descriptor numbers to pass to the underlying `graphviz`
library. There is a standard method to get them, `fileno()`, but, under
Python 2, it doesn't call that method. Instead, it makes sure it's working with
a builtin `file` object and does a direct translation.

Under gevent, subprocess.Popen returns cooperative pipes, not file objects. So the
check fails and you get this error message
(https://github.com/pygraphviz/pygraphviz/blob/master/pygraphviz/graphviz_wrap.c).

Even if we could teach `pygraphviz` to call `fileno()`, however, it's unlikely to be
successful because the underlying graphviz library is going to assume blocking IO. So
the only solution I see is to let `pygraphviz` use the original blocking Popen. That's
easy to accomplish, but if you stop there you get a hang. This is because it uses threads
to read from the pipes; the threads are cooperative, but the pipes aren't, so the event loop hangs.
"""

def patch( scope, original, replacement ):

	if not hasattr( gevent, 'monkey' ):
		# Probably only in unit tests.
		return

	class FakeSubprocess(object):
		pass

	import subprocess
	fake_subprocess = FakeSubprocess()

	for k, v in subprocess.__dict__.items():
		setattr(fake_subprocess, k, v)

	for k, v in gevent.monkey.saved['subprocess'].items():
		setattr(fake_subprocess, k, v)

	import pygraphviz.agraph
	pygraphviz.agraph.subprocess = fake_subprocess

	pygraphviz.agraph.PipeReader.__bases__ = (gevent.monkey.get_original('threading', 'Thread'),)

	start = pygraphviz.agraph.PipeReader.start
	def _start(self):
		pass
	_start.__code__ = start.__code__
	for k, v in start.func_globals.items():
		_start.func_globals[k] = v
	_start.func_globals['_start_new_thread'] = gevent.monkey.get_original('threading', '_start_new_thread')
	pygraphviz.agraph.PipeReader.start = _start

	import threading

	Lock = gevent.monkey.get_original('threading', 'Lock')

	class _Condition(threading._Condition):
		def wait(self, timeout=None):
			pass

	_Condition.wait.im_func.__code__ = threading._Condition.wait.im_func.__code__
	_Condition.wait.im_func.func_globals['_allocate_lock'] = gevent.monkey.get_original('threading', '_allocate_lock')

	def Event():
		evt = threading.Event()
		evt._Event__cond = _Condition(Lock())
		return evt

	init = pygraphviz.agraph.PipeReader.__init__
	def __init__(self, *args, **kwargs):
		init(self, *args, **kwargs)
		self._Thread__started = Event()
		self._Thread__block = _Condition(Lock())
	pygraphviz.agraph.PipeReader.__init__ = __init__

	# Since we're using real threads, we should use real locks
	# (instead of gevent locks) to avoid intermittent 'block forever'
	# issues.
	if isinstance( threading._active_limbo_lock, gevent.lock.Semaphore ):
		threading._active_limbo_lock = gevent.monkey.get_original('threading', '_allocate_lock')()
