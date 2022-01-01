"""A setup tools based setup module.
"""

import setuptools


setuptools.setup(
    name="mpytool",
    version="1.0",
    description="MicroPython tool",
    long_description=(
        "Control device running MicroPython over serial line. "
        "Allow list files, upload, download, delete, ... "
        "Project page: https://github.com/pavelrevak/mpytool"),
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