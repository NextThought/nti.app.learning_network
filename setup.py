import codecs
from setuptools import setup, find_packages

VERSION = '0.0.0'

entry_points = {
	"z3c.autoinclude.plugin": [
		'target = nti.app',
	],
	'console_scripts': [
	],
}

import platform
py_impl = getattr(platform, 'python_implementation', lambda: None)
IS_PYPY = py_impl() == 'PyPy'

setup(
	name='nti.app.learning_network',
	version=VERSION,
	author='Josh Zuech',
	author_email='josh.zuech@nextthought.com',
	description="NTI app learning_network",
	long_description=codecs.open('README.rst', encoding='utf-8').read(),
	license='Proprietary',
	keywords='pyramid preference',
	classifiers=[
		'Intended Audience :: Developers',
		'Natural Language :: English',
		'Programming Language :: Python :: 2.7',
		'Programming Language :: Python :: 3',
		'Programming Language :: Python :: 3.3',
	],
	packages=find_packages('src'),
	package_dir={'': 'src'},
	namespace_packages=['nti', 'nti.app'],
	install_requires=[
		'collective.monkeypatcher',
		'setuptools',
		 # 'pygraphviz' if not IS_PYPY else '',
		'nti.learning_network',
		'nti.app.analytics_registration'
	],
	entry_points=entry_points
)
