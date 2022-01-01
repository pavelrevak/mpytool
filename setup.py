"""A setup tools based setup module.
"""

import setuptools


_DESCRIPTION = "MPY tool - manage files on devices running MicroPython"
_LONG_DESCRIPTION = _DESCRIPTION + """

https://github.com/pavelrevak/mpytool"""

setuptools.setup(
    name="mpytool",
    version="1.0.0",
    description=_DESCRIPTION,
    long_description=_LONG_DESCRIPTION,
    url="https://github.com/pavelrevak/mpytool",
    author="Pavel Revak",
    author_email="pavel.revak@gmail.com",
    license="MIT",
    keywords="mpy micropython",

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
