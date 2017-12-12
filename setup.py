import codecs
from setuptools import setup
from setuptools import find_packages

entry_points = {
    "z3c.autoinclude.plugin": [
        'target = nti.app',
    ],
}


TESTS_REQUIRE = [
    'nti.app.testing',
    'nti.testing',
    'zope.testrunner',
]

import platform
py_impl = getattr(platform, 'python_implementation', lambda: None)
IS_PYPY = py_impl() == 'PyPy'


def _read(fname):
    with codecs.open(fname, encoding='utf-8') as f:
        return f.read()


setup(
    name='nti.app.learning_network',
    version=_read('version.txt').strip(),
    author='Josh Zuech',
    author_email='josh.zuech@nextthought.com',
    description="Application Layer Learning Network",
    long_description=_read('README.rst'),
    license='Apache',
    keywords='pyramid analytics learning network',
    classifiers=[
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
    ],
    url="https://github.com/NextThought/nti.app.learning_network",
    zip_safe=True,
    packages=find_packages('src'),
    package_dir={'': 'src'},
    include_package_data=True,
    namespace_packages=['nti', 'nti.app'],
    tests_require=TESTS_REQUIRE,
    install_requires=[
        'setuptools',
        'collective.monkeypatcher',
        'gevent',
        'nti.analytics',
        'nti.app.assessment',
        'nti.common',
        'nti.contentfragments',
        'nti.contenttypes.courses',
        'nti.externalization',
        'nti.learning_network',
        'nti.ntiids',
        'pyramid',
        'requests',
        'zope.cachedescriptors',
        'zope.component',
        'zope.interface',
        'zope.security',
    ],
    extras_require={
        'test': TESTS_REQUIRE,
        'docs': [
            'Sphinx',
            'repoze.sphinx.autointerface',
            'sphinx_rtd_theme',
        ],
    },
    entry_points=entry_points,
)
