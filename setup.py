#!/usr/bin/env python

from setuptools import find_packages, setup

setup(
    name='api-integration',
    version='5.0.2',
    description='RESTful api integration for edX platform',
    long_description=open('README.rst').read(),
    author='edX',
    url='https://github.com/edx-solutions/api-integration.git',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'Django>=2.2,<2.3',
    ],
)
