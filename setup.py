"""A setup tools based setup module.
"""

import setuptools


_ABOUT = {}

exec(open('mpytool/__about__.py').read(), _ABOUT)


setuptools.setup(
    name=_ABOUT['APP_NAME'],
    version=_ABOUT['VERSION'],
    description=_ABOUT['DESCRIPTION'],
    long_description=_ABOUT['LONG_DESCRIPTION'],
    url=_ABOUT['URL'],
    author=_ABOUT['AUTHOR'],
    author_email=_ABOUT['AUTHOR_EMAIL'],
    license=_ABOUT['LICENSE'],
    keywords=_ABOUT['KEYWORDS'],

    classifiers=[
        # https://pypi.python.org/pypi?%3Aaction=list_classifiers
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Embedded Systems',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
    ],

    python_requires='>3.5',

    packages=[
        'mpytool',
    ],

    install_requires=[
        'pyserial (>=3.0)'
    ],

    entry_points={
        'console_scripts': [
            'mpytool=mpytool.mpytool:main',
        ],
    },
)
