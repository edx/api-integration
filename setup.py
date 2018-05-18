#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name='api-integration',
    version='1.7.9',
    description='RESTful api integration for edX platform',
    long_description=open('README.rst').read(),
    author='edX',
    url='https://github.com/edx-solutions/api-integration.git',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'django>=1.8',
        'six',
    ],
)
