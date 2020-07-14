
#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name='api-integration',
    version='4.0.6',
    description='RESTful api integration for edX platform',
    long_description=open('README.rst').read(),
    author='edX',
    url='https://github.com/edx-solutions/api-integration.git',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'Django>=1.11,<1.12',
        'six',
    ],
)
